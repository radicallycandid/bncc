"""
Teste de ponta a ponta do pipeline de scoring usando chamadas síncronas
(messages.create, NÃO batch). Roda em segundos e custa < $0.01.

O que este script testa:
  ✓ Carregamento e parsing do prompt (prompts/prereq_judge.md)
  ✓ Montagem das mensagens com os dados reais do CSV
  ✓ Resposta do modelo e parsing do JSON retornado
  ✓ Lógica de Pass 1 → borderline → Pass 2 → escalonamento
  ✓ Lógica de assembly (decide() de batch_utils)
  ✓ Correção de simetria (apply_consistency() de batch_utils)

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
    apply_consistency,
    custom_id,
    load_prompt,
    make_request,
    parse_response,
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


def run_two_pass(client, rows: list[dict], system: str, user_tpl: str, model_primary: str) -> dict:
    """
    Simula a lógica de Pass 1 → Pass 2 → Pass 3 de forma síncrona.
    Retorna {custom_id: {score, label, source, reasoning}}.
    """
    results: dict[str, dict] = {}
    n = len(rows)

    print(f"\n── Pass 1 ({model_primary}, temp=0) — {n} pares ──")
    for i, row in enumerate(rows, 1):
        cid = custom_id(row)
        res = call_model(client, row, system, user_tpl, model_primary, temperature=0)
        if res:
            res["source"] = "pass1"
            results[cid] = res
            bar = score_bar(res["score"])
            print(f"  [{i:2d}/{n}] {cid:<25s} {bar} {res['label']}")
        else:
            print(f"  [{i:2d}/{n}] {cid:<25s} PARSE ERROR")
        time.sleep(0.2)  # evita rate-limit em rajadas

    # Pass 2: borderlines
    borderlines = [
        row for row in rows
        if results.get(custom_id(row), {}).get("label")
        in BORDERLINE_LABELS
    ]
    if borderlines:
        print(f"\n── Pass 2 ({model_primary}, temp=0.5) — {len(borderlines)} borderlines ──")
        for i, row in enumerate(borderlines, 1):
            cid = custom_id(row)
            res2 = call_model(client, row, system, user_tpl, model_primary, temperature=0.5)
            if not res2:
                print(f"  [{i}] {cid} PARSE ERROR (mantendo Pass 1)")
                continue
            s1 = results[cid]["score"]
            s2 = res2["score"]
            delta = abs(s1 - s2)
            if delta <= ESCALATION_THRESHOLD:
                avg = round((s1 + s2) / 2, 4)
                results[cid] = {**res2, "score": avg, "source": "pass1+pass2_avg"}
                results[cid].pop("label", None)
                action = f"avg={avg}"
            else:
                # Escala para Sonnet
                note = (
                    f"[Nota: dois julgamentos discordaram.\n"
                    f"Julgamento 1: {results[cid]['label']} ({s1}) — \"{results[cid]['reasoning']}\"\n"
                    f"Julgamento 2: {res2['label']} ({s2}) — \"{res2['reasoning']}\"\n"
                    "Avalie independentemente.]"
                )
                res3 = call_model(client, row, system, user_tpl, MODEL_SECONDARY,
                                  temperature=0, context_note=note)
                if res3:
                    results[cid] = {**res3, "source": "pass3"}
                    action = f"→ Sonnet: {res3['label']}"
                else:
                    avg = round((s1 + s2) / 2, 4)
                    results[cid] = {**res2, "score": avg, "source": "pass1+pass2_fallback"}
                    results[cid].pop("label", None)
                    action = f"Sonnet falhou, fallback avg={avg}"
            bar = score_bar(results[cid]["score"])
            lbl = results[cid].get("label", "")
            print(f"  [{i}] {cid:<25s} {bar} {lbl}  ({action})")
            time.sleep(0.3)
    else:
        print("\n  Nenhum borderline encontrado.")

    return results


def print_report(rows: list[dict], final: dict) -> None:
    print("\n" + "═" * 80)
    print("RESULTADO FINAL")
    print("═" * 80)
    for row in rows:
        cid = custom_id(row)
        res = final.get(cid)
        if not res:
            continue
        bar  = score_bar(res["score"])
        sym  = " [sym]" if res.get("consistency") == "corrected" else ""
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

    results        = run_two_pass(client, rows, system, user_tpl, args.model_primary)
    final, n_sym   = apply_consistency(rows, results)
    if n_sym:
        print(f"\n── Consistência: {n_sym // 2} par(es) simétrico(s) corrigido(s) ──")
    print_report(rows, final)


if __name__ == "__main__":
    main()
