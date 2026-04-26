"""
Utilitários compartilhados por todos os scripts do pipeline de scoring.

Pipeline completo (rodar em ordem):
  submit_pass1.py   Submete todos os 22 k pares ao Haiku (temp=0)
  poll.py           Verifica status e baixa resultados prontos
  submit_pass2.py   Submete borderlines ao Haiku (temp=0.5)
  poll.py           (de novo)
  submit_pass3.py   Submete discordâncias ao Sonnet (temp=0)
  poll.py           (de novo)
  assemble.py       Consolida tudo em prereq_pairs_scored.csv
  consistency.py    Corrige simetria de pares delta=0 → prereq_pairs_final.csv

Pré-requisito: export ANTHROPIC_API_KEY=sk-ant-...
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Modelos e hiperparâmetros
# ---------------------------------------------------------------------------

MODEL_PRIMARY   = "claude-haiku-4-5-20251001"   # Haiku 4.5
MODEL_SECONDARY = "claude-sonnet-4-6"            # Sonnet 4.6

# Labels borderline → vão para o Pass 2
BORDERLINE_LABELS = {"PROVAVELMENTE_SIM", "INCERTO", "PROVAVELMENTE_NÃO"}

# Diferença de score entre Pass 1 e Pass 2 que dispara escalação para Sonnet
ESCALATION_THRESHOLD = 0.25

# Limite de requests por batch (hard limit da API)
BATCH_SIZE = 10_000

# Mapeamento canônico label → score
LABEL_TO_SCORE: dict[str, float] = {
    "DEFINITIVAMENTE_SIM":  1.00,
    "PROVAVELMENTE_SIM":    0.75,
    "INCERTO":              0.50,
    "PROVAVELMENTE_NÃO":    0.25,
    "DEFINITIVAMENTE_NÃO":  0.00,
}
VALID_LABELS = set(LABEL_TO_SCORE)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT         = Path(__file__).parent.parent   # raiz do repositório
PAIRS_CSV    = ROOT / "data" / "prereq_pairs_bncc.csv"
STATE_FILE   = ROOT / "data" / "batch_state.json"
RAW_DIR      = ROOT / "data" / "raw_results"
INTERIM_DIR  = ROOT / "data" / "interim"
PROMPT_FILE  = ROOT / "prompts" / "prereq_judge.md"

RAW_DIR.mkdir(parents=True, exist_ok=True)
INTERIM_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Carregamento do prompt
# ---------------------------------------------------------------------------

def load_prompt() -> tuple[str, str]:
    """
    Lê prompts/prereq_judge.md e retorna (system_prompt, user_template).
    Extrai o conteúdo dos blocos de código sob ## SYSTEM e ## USER.
    """
    text = PROMPT_FILE.read_text(encoding="utf-8")
    blocks = re.findall(r"```\n(.*?)\n```", text, re.DOTALL)
    if len(blocks) < 2:
        raise ValueError(f"Não encontrei blocos SYSTEM e USER em {PROMPT_FILE}")
    return blocks[0], blocks[1]


def fill_user(template: str, row: dict) -> str:
    """Substitui as variáveis {{...}} no template USER com os dados do par."""
    return (
        template
        .replace("{{ANO_A}}",        str(row["ano_a"]))
        .replace("{{CODIGO_A}}",     row["codigo_a"])
        .replace("{{HABILIDADE_A}}", row["habilidade_a"])
        .replace("{{ANO_B}}",        str(row["ano_b"]))
        .replace("{{CODIGO_B}}",     row["codigo_b"])
        .replace("{{HABILIDADE_B}}", row["habilidade_b"])
    )


# ---------------------------------------------------------------------------
# Construção de requests
# ---------------------------------------------------------------------------

def custom_id(row: dict) -> str:
    return f"{row['codigo_a']}__{row['codigo_b']}"


def make_request(
    row: dict,
    system: str,
    user_template: str,
    model: str,
    temperature: float,
    context_note: str | None = None,
) -> dict:
    """
    Monta um request para a Message Batches API.
    context_note é um parágrafo opcional que prefacia o USER (usado no Pass 3
    para informar o Sonnet sobre os julgamentos anteriores discordantes).
    """
    user_content = fill_user(user_template, row)
    if context_note:
        user_content = context_note + "\n\n" + user_content

    # System prompt é idêntico em todas as ~22k requisições. Marcado para
    # prompt caching para reduzir custo/latência sempre que o prefixo atingir
    # o mínimo cacheável do modelo (Sonnet 4.6: 2048 tokens; Haiku 4.5: 4096).
    return {
        "custom_id": custom_id(row),
        "params": {
            "model":       model,
            "max_tokens":  512,
            "temperature": temperature,
            "system":      [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages":    [{"role": "user", "content": user_content}],
        },
    }


# ---------------------------------------------------------------------------
# Parsing de respostas
# ---------------------------------------------------------------------------

def parse_response(text: str) -> dict | None:
    """
    Extrai {reasoning, label} do texto de resposta do modelo e acrescenta
    o score canônico mapeado a partir do label.

    O modelo só precisa produzir reasoning + label. O número 0–1 é derivado
    aqui com mapeamento fixo, garantindo consistência nas etapas de averaging.
    Scores intermediários (ex.: 0.125) só surgem de médias entre passes — nunca
    do modelo diretamente — e são o valor final: não precisam voltar a ser label.

    Usa matching de colchetes para encontrar todos os objetos JSON no texto e
    retorna o último válido (trata casos em que o modelo emite auto-correção
    com dois blocos JSON, onde o segundo é o veredito final).
    """
    try:
        starts = [m.start() for m in re.finditer(r"\{", text)]
        for start in reversed(starts):
            depth, end = 0, -1
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            if end == -1:
                continue
            try:
                data = json.loads(text[start : end + 1])
            except (json.JSONDecodeError, TypeError):
                continue
            if not {"reasoning", "label"}.issubset(data):
                continue
            if data["label"] not in VALID_LABELS:
                continue
            data["score"] = LABEL_TO_SCORE[data["label"]]
            return data
    except (TypeError, KeyError):
        pass
    return None


def load_jsonl_results(pass_n: int) -> dict[str, dict]:
    """
    Lê todos os arquivos JSONL baixados para o pass indicado.
    Retorna {custom_id: parsed_result}; ignora erros de API e falhas de parsing.
    """
    state = load_state()
    files = [
        Path(b["results_file"])
        for b in state["batches"]
        if b["pass"] == pass_n and b["status"] == "downloaded"
    ]
    scores: dict[str, dict] = {}
    n_errors = 0
    for path in files:
        for line in path.open(encoding="utf-8"):
            result = json.loads(line)
            cid = result["custom_id"]
            if result["result"]["type"] != "succeeded":
                n_errors += 1
                continue
            text = result["result"]["message"]["content"][0]["text"]
            parsed = parse_response(text)
            if parsed:
                scores[cid] = parsed
            else:
                n_errors += 1
    print(f"  Pass {pass_n}: {len(scores):,} válidos, {n_errors:,} erros/unparseable")
    return scores


# ---------------------------------------------------------------------------
# Gerenciamento de estado
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"batches": []}
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or not isinstance(state.get("batches"), list):
        raise ValueError(
            f"{STATE_FILE} corrompido: esperado dict com chave 'batches' (lista). "
            f"Inspecione ou apague o arquivo para reiniciar do zero."
        )
    return state


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Submissão de batches
# ---------------------------------------------------------------------------

def submit_batches(
    client,
    requests: list[dict],
    pass_n: int,
    state: dict,
) -> int:
    """
    Divide requests em chunks de BATCH_SIZE, submete cada um à Batch API,
    atualiza state e salva. Retorna o número de batches submetidos.
    """
    chunks = [
        requests[i : i + BATCH_SIZE]
        for i in range(0, len(requests), BATCH_SIZE)
    ]
    for i, chunk in enumerate(chunks, 1):
        print(f"  Submetendo batch {i}/{len(chunks)} ({len(chunk):,} requests)…", flush=True)
        batch = client.messages.batches.create(requests=chunk)
        state["batches"].append({
            "pass":         pass_n,
            "batch_id":     batch.id,
            "status":       "in_progress",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "n_requests":   len(chunk),
            "results_file": None,
        })
        save_state(state)
        print(f"    → {batch.id}")
    return len(chunks)


# ---------------------------------------------------------------------------
# Lógica de decisão e pós-processamento
# ---------------------------------------------------------------------------

def decide(
    cid: str,
    pass1: dict,
    pass2: dict,
    pass3: dict,
) -> tuple[float | None, str]:
    """
    Retorna (score_final, source) para um par.

    O score é um float em [0, 1]. Pode ser não-canônico (ex.: 0.125) quando
    resulta de uma média entre passes — esse float é o valor definitivo e não
    é convertido de volta para um label de palavras.
    """
    if cid not in pass1:
        return None, "missing"

    label1 = pass1[cid]["label"]
    score1 = pass1[cid]["score"]

    if label1 not in BORDERLINE_LABELS:
        return score1, "pass1"

    if cid not in pass2:
        return score1, "pass1"

    score2 = pass2[cid]["score"]

    if abs(score1 - score2) <= ESCALATION_THRESHOLD:
        return round((score1 + score2) / 2, 4), "pass1+pass2_avg"

    if cid in pass3:
        return pass3[cid]["score"], "pass3"

    return round((score1 + score2) / 2, 4), "pass1+pass2_fallback"


def apply_consistency(rows: list[dict], results: dict) -> tuple[dict, int]:
    """
    Aplica correção de simetria a pares delta=0: score(A→B) = (raw(A→B) + (1 − raw(B→A))) / 2.

    Corrige apenas quando raw(A→B) + raw(B→A) > 1.0 (inconsistência real).
    Retorna (dicionário atualizado, número de pares corrigidos).
    """
    corrected = dict(results)
    n_corrected = 0
    for row in rows:
        cid_ab = custom_id(row)
        cid_ba = f"{row['codigo_b']}__{row['codigo_a']}"
        if int(row["ano_a"]) != int(row["ano_b"]):
            continue
        if cid_ab not in results or cid_ba not in results:
            continue
        raw_ab = results[cid_ab]["score"]
        raw_ba = results[cid_ba]["score"]
        if raw_ab + raw_ba <= 1.0:
            continue
        c_ab = round((raw_ab + (1.0 - raw_ba)) / 2, 4)
        c_ba = round((raw_ba + (1.0 - raw_ab)) / 2, 4)
        corrected[cid_ab] = {**corrected[cid_ab], "score": c_ab, "consistency": "corrected"}
        corrected[cid_ab].pop("label", None)
        corrected[cid_ba] = {**corrected[cid_ba], "score": c_ba, "consistency": "corrected"}
        corrected[cid_ba].pop("label", None)
        n_corrected += 1
    return corrected, n_corrected
