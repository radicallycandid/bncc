"""
Teste de ponta a ponta do pipeline de scoring usando chamadas síncronas
(messages.create, NÃO batch). Roda em segundos e custa < $0.01.

O que este script testa:
  ✓ Carregamento e parsing do prompt (prompts/prereq_judge.md)
  ✓ Montagem das mensagens com os dados reais do CSV
  ✓ Resposta do modelo e parsing do JSON retornado
  ✓ Lógica de Pass 1 → borderline → Pass 2 → escalonamento
  ✓ Lógica de assembly (decide() de batch_utils)
  ✓ Correção de simetria (resolve_pair() de batch_utils, caminho algébrico —
    o sample não roda o pass simétrico, então sym_results fica vazio)

O que NÃO é testado aqui (mas funciona de forma análoga):
  - Submissão/polling/download da Batch API
    → Para isso, rode: python scripts/submit_pass1.py --limit 10

Uso:
  python scripts/run_sample.py [--nrows N] [--model-primary M]

  --nrows N         Número de pares a testar (default: 10)
  --model-primary   Modelo primário (default: claude-haiku-4-5-20251001)
"""

import argparse
import csv
import os
import random
import time
from collections import Counter

import anthropic

from batch_utils import (
    PAIRS_CSV,
    MODEL_PRIMARY,
    MODEL_SECONDARY,
    BORDERLINE_LABELS,
    ESCALATION_THRESHOLD,
    custom_id,
    decide,
    load_prompt,
    make_request,
    parse_response,
    resolve_pair,
)

def score_bar(score: float) -> str:
    """Barra de 5 blocos proporcional ao score — funciona para qualquer float em [0,1]."""
    filled = round(score * 5)
    return "█" * filled + "░" * (5 - filled)


def call_model(
    client,
    row: dict,
    system: str,
    user_tpl: str,
    model: str,
    temperature: float,
    context_note: str | None = None,
) -> dict | None:
    """Chama messages.create reusando o shape de make_request.

    Single source of truth: qualquer mudança no formato (cache_control, max_tokens,
    context_note) propaga automaticamente do batch path pro sync path.
    """
    req = make_request(row, system, user_tpl, model, temperature, context_note=context_note)
    resp = client.messages.create(**req["params"])
    return parse_response(resp.content[0].text)


def select_sample(nrows: int, seed: int = 42) -> list[dict]:
    """
    Amostragem aleatória estratificada por (delta × faixa de ano), garantindo:
    - Cobertura de delta=0, 1 e 2
    - Cobertura de anos diferentes (não só ano 1)
    - Cobertura de pares EF e EM
    - Para cada par delta=0 selecionado, inclui também o par simétrico
    """
    rng = random.Random(seed)

    all_rows = list(csv.DictReader(PAIRS_CSV.open(encoding="utf-8")))

    # Estratos: (delta, ano_a) — distribui a amostra pelo espaço do problema
    strata: dict[tuple, list] = {}
    for row in all_rows:
        delta = int(row["ano_b"]) - int(row["ano_a"])
        key = (delta, int(row["ano_a"]))
        strata.setdefault(key, []).append(row)

    # Embaralha cada estrato
    for v in strata.values():
        rng.shuffle(v)

    # Coleta 1 par por estrato até atingir nrows, priorizando delta variado
    per_delta = max(1, nrows // 3)
    selected: list[dict] = []
    for delta in (0, 1, 2):
        keys = sorted(k for k in strata if k[0] == delta)
        rng.shuffle(keys)
        for key in keys:
            if len([r for r in selected if int(r["ano_b"]) - int(r["ano_a"]) == delta]) >= per_delta:
                break
            if strata[key]:
                selected.append(strata[key].pop())

    # Complementa com pares aleatórios se ainda faltar
    remaining = [r for rows in strata.values() for r in rows]
    rng.shuffle(remaining)
    for row in remaining:
        if len(selected) >= nrows:
            break
        selected.append(row)

    # Para cada delta=0 selecionado, garante que o par simétrico também entre
    # (necessário para a etapa de consistência ter algo para corrigir)
    delta0_selected = {(r["codigo_a"], r["codigo_b"]) for r in selected
                       if int(r["ano_a"]) == int(r["ano_b"])}
    index = {(r["codigo_a"], r["codigo_b"]): r for r in all_rows}
    for (a, b) in list(delta0_selected):
        sym = index.get((b, a))
        if sym and sym not in selected:
            selected.append(sym)

    return selected


def run_passes(
    client,
    rows: list[dict],
    system: str,
    user_tpl: str,
    model_primary: str,
) -> tuple[dict, dict, dict]:
    """
    Executa os três passes de forma síncrona e devolve (pass1, pass2, pass3),
    cada um sendo {custom_id: parsed_result}. A resolução final é delegada a
    decide() — este script só coleta os ingredientes brutos.
    """
    pass1: dict[str, dict] = {}
    pass2: dict[str, dict] = {}
    pass3: dict[str, dict] = {}
    n = len(rows)

    print(f"\n── Pass 1 ({model_primary}, temp=0) — {n} pares ──")
    for i, row in enumerate(rows, 1):
        cid = custom_id(row)
        res = call_model(client, row, system, user_tpl, model_primary, temperature=0)
        if res:
            pass1[cid] = res
            bar = score_bar(res["score"])
            print(f"  [{i:2d}/{n}] {cid:<25s} {bar} {res['label']}")
        else:
            print(f"  [{i:2d}/{n}] {cid:<25s} PARSE ERROR")
        time.sleep(0.2)  # evita rate-limit em rajadas

    borderlines = [row for row in rows
                   if pass1.get(custom_id(row), {}).get("label") in BORDERLINE_LABELS]
    if not borderlines:
        print("\n  Nenhum borderline encontrado.")
        return pass1, pass2, pass3

    print(f"\n── Pass 2 ({model_primary}, temp=0.5) — {len(borderlines)} borderlines ──")
    for i, row in enumerate(borderlines, 1):
        cid = custom_id(row)
        res2 = call_model(client, row, system, user_tpl, model_primary, temperature=0.5)
        if not res2:
            print(f"  [{i}] {cid} PARSE ERROR (mantendo Pass 1)")
            continue
        pass2[cid] = res2

        s1 = pass1[cid]["score"]
        s2 = res2["score"]
        if abs(s1 - s2) <= ESCALATION_THRESHOLD:
            print(f"  [{i}] {cid:<25s} pass2={res2['label']} (avg será aplicado)")
            time.sleep(0.3)
            continue

        # Escala para Sonnet — replica o context_note usado pelo submit_pass3
        note = (
            f"[Nota: dois julgamentos discordaram.\n"
            f"Julgamento 1: {pass1[cid]['label']} ({s1}) — \"{pass1[cid]['reasoning']}\"\n"
            f"Julgamento 2: {res2['label']} ({s2}) — \"{res2['reasoning']}\"\n"
            "Avalie independentemente.]"
        )
        res3 = call_model(client, row, system, user_tpl, MODEL_SECONDARY,
                          temperature=0, context_note=note)
        if res3:
            pass3[cid] = res3
            print(f"  [{i}] {cid:<25s} pass2={res2['label']} → Sonnet: {res3['label']}")
        else:
            print(f"  [{i}] {cid:<25s} pass2={res2['label']} → Sonnet falhou (fallback avg)")
        time.sleep(0.3)

    return pass1, pass2, pass3


def assemble_and_resolve(
    rows: list[dict],
    pass1: dict,
    pass2: dict,
    pass3: dict,
) -> dict[str, dict]:
    """
    Roda decide() em cada par, depois resolve_pair() para corrigir simetria.
    Como o sample não executa o pass simétrico, sym_results fica vazio e o
    caminho algébrico é o que se aplica.
    """
    decided: dict[str, tuple[float | None, str]] = {}
    raw_scores: dict[tuple, float | None] = {}
    for row in rows:
        cid = custom_id(row)
        score, source = decide(cid, pass1, pass2, pass3)
        decided[cid] = (score, source)
        raw_scores[(row["codigo_a"], row["codigo_b"])] = score

    final: dict[str, dict] = {}
    for row in rows:
        ca, cb = row["codigo_a"], row["codigo_b"]
        cid = f"{ca}__{cb}"
        score, source = decided[cid]
        if score is None:
            continue

        score_resolved, source_resolved, flag = resolve_pair(
            raw_ab=score,
            raw_ba=raw_scores.get((cb, ca)),
            same_year=int(row["ano_a"]) == int(row["ano_b"]),
            cid_ab=cid,
            current_source=source,
            sym_results={},
        )

        # Mescla reasoning/label dos passes brutos para o relatório
        last = pass3.get(cid) or pass2.get(cid) or pass1.get(cid, {})
        entry = {
            "score": score_resolved if score_resolved is not None else score,
            "source": source_resolved,
            "consistency_flag": flag,
            "reasoning": last.get("reasoning", ""),
        }
        if source_resolved == source and "label" in last and source in {"pass1", "pass3"}:
            entry["label"] = last["label"]
        final[cid] = entry
    return final


def print_report(rows: list[dict], final: dict) -> None:
    print("\n" + "═" * 80)
    print("RESULTADO FINAL")
    print("═" * 80)
    for row in rows:
        cid = custom_id(row)
        res = final.get(cid)
        if not res:
            continue
        bar = score_bar(res["score"])
        sym = " [sym]" if res.get("consistency_flag") == "symmetric_corrected" else ""
        label_str = f" {res['label']}" if "label" in res else ""
        print(f"\n{cid}{sym}")
        print(f"  A (ano {row['ano_a']}): {row['habilidade_a'][:72]}…")
        print(f"  B (ano {row['ano_b']}): {row['habilidade_b'][:72]}…")
        print(f"  {bar}{label_str} (score={res['score']}, via {res['source']})")
        print(f"  Reasoning: {res['reasoning'][:100]}…")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nrows", type=int, default=10, help="Número de pares a testar")
    parser.add_argument("--model-primary", default=MODEL_PRIMARY)
    args = parser.parse_args()

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    system, user_tpl = load_prompt()

    rows = select_sample(args.nrows)
    print(f"Amostra selecionada: {len(rows)} pares")
    delta_dist = Counter(int(r["ano_b"]) - int(r["ano_a"]) for r in rows)
    for d, n in sorted(delta_dist.items()):
        print(f"  delta={d}: {n} pares")

    pass1, pass2, pass3 = run_passes(client, rows, system, user_tpl, args.model_primary)
    final = assemble_and_resolve(rows, pass1, pass2, pass3)

    n_sym = sum(1 for r in final.values() if r.get("consistency_flag") == "symmetric_corrected")
    if n_sym:
        print(f"\n── Consistência: {n_sym // 2} par(es) simétrico(s) corrigido(s) ──")
    print_report(rows, final)


if __name__ == "__main__":
    main()
