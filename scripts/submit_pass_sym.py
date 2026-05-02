"""
Pass Simétrico — re-scoring de pares delta=0 inconsistentes com o Sonnet.

Pares elegíveis: delta=0 (mesmo ano escolar) onde score(A→B) + score(B→A) > 1.0
segundo o prereq_pairs_scored.csv gerado por assemble.py.

Para cada par elegível, submete UM único request ao Sonnet usando o prompt
simétrico (prereq_judge_sym.md), que pede os dois labels de uma só vez.
O modelo raciocina sobre ambas as direções com a restrição explícita de que
a soma dos scores deve ser ≤ 1.0 — mas pode atribuir valores baixos a ambas
se as habilidades forem de domínios distintos.

IDs de request: "{menor_codigo}__{maior_codigo}__sym" (ordem canônica).
Resultados: carregados por load_jsonl_results_sym() em batch_utils.

Uso:
    python scripts/submit_pass_sym.py
    (somente após assemble.py ter gerado data/prereq_pairs_scored.csv)
"""

import csv
import os
from pathlib import Path

import anthropic

from batch_utils import (
    MODEL_SYM,
    PROMPT_FILE_SYM,
    load_prompt,
    load_state,
    make_request_sym,
    submit_batches,
)

SCORED_CSV = Path(__file__).parent.parent / "data" / "prereq_pairs_scored.csv"


def canonicalize_sym_row(ca: str, cb: str, row_by_key: dict) -> dict | None:
    """
    Devolve o row do par não-ordenado {ca, cb} reescrito com codigo_a < codigo_b,
    a ordem canônica usada por make_request_sym. Procura primeiro a direção
    canônica em `row_by_key` e, se só achar a invertida, troca os campos a/b.

    Retorna None se nenhuma das duas direções existir em row_by_key.

    Premissa: o par é delta=0 (chamado apenas pelo pass simétrico). Garantida
    por um assert que ataca o resultado final — pares com ano_a != ano_b
    indicam que o caller filtrou errado.
    """
    ca_can, cb_can = sorted([ca, cb])
    row_can = row_by_key.get((ca_can, cb_can)) or row_by_key.get((cb_can, ca_can))
    if row_can is None:
        return None
    if ca_can != row_can["codigo_a"]:
        row_can = {
            "codigo_a":     ca_can,
            "ano_a":        row_can["ano_b"],
            "habilidade_a": row_can["habilidade_b"],
            "codigo_b":     cb_can,
            "ano_b":        row_can["ano_a"],
            "habilidade_b": row_can["habilidade_a"],
        }
    assert row_can["ano_a"] == row_can["ano_b"], "sym pass requires delta=0 pairs"
    return row_can


def main() -> None:
    state = load_state()

    if any(b["pass"] == "sym" for b in state["batches"]):
        print("Batches do Pass Simétrico já existem. Abortando.")
        return

    if not SCORED_CSV.exists():
        print(f"Arquivo não encontrado: {SCORED_CSV}")
        print("Rode assemble.py primeiro.")
        return

    print("Carregando prereq_pairs_scored.csv…")
    rows = list(csv.DictReader(SCORED_CSV.open(encoding="utf-8")))

    # Indexa score por (codigo_a, codigo_b)
    scores: dict[tuple, float] = {}
    row_by_key: dict[tuple, dict] = {}
    for row in rows:
        key = (row["codigo_a"], row["codigo_b"])
        try:
            scores[key] = float(row["score"])
        except (ValueError, TypeError):
            pass
        row_by_key[key] = row

    # Identifica pares delta=0 com inconsistência (soma > 1.0).
    # Itera uma vez; cada par não-ordenado é processado exatamente uma vez.
    seen: set[frozenset] = set()
    inconsistent: list[dict] = []

    for row in rows:
        ca, cb = row["codigo_a"], row["codigo_b"]
        if int(row["ano_a"]) != int(row["ano_b"]):
            continue
        pair = frozenset([ca, cb])
        if pair in seen:
            continue
        seen.add(pair)

        s_ab = scores.get((ca, cb))
        s_ba = scores.get((cb, ca))
        if s_ab is None or s_ba is None:
            continue
        if s_ab + s_ba <= 1.0:
            continue

        row_can = canonicalize_sym_row(ca, cb, row_by_key)
        if row_can is None:
            continue
        inconsistent.append(row_can)

    print(f"Pares delta=0 inconsistentes: {len(inconsistent):,}")

    if not inconsistent:
        print("Nenhum par inconsistente encontrado. Rode consistency.py diretamente.")
        return

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    system, user_tpl = load_prompt(PROMPT_FILE_SYM)

    requests = [
        make_request_sym(row, system, user_tpl, MODEL_SYM, temperature=0)
        for row in inconsistent
    ]

    n = submit_batches(client, requests, pass_n="sym", state=state)
    print(f"\n{n} batch(es) submetido(s). Rode poll.py, depois consistency.py.")


if __name__ == "__main__":
    main()
