"""
Pass 2 — resubmete os pares "borderline" do Pass 1 ao Haiku com temperature=0.5.

Borderline = label em {PROVAVELMENTE_SIM, INCERTO, PROVAVELMENTE_NÃO}.

A temperatura mais alta introduz variação deliberada: se o Pass 2 concordar com o
Pass 1, aumenta a confiança; se discordar significativamente, o par é escalado ao
Sonnet no Pass 3.

Também salva data/interim/pass1_scores.csv com os resultados do Pass 1 para todos
os pares (útil para auditoria e para o 5_assemble.py).

Uso:
    python scripts/3_submit_pass2.py
    (somente após 2_poll.py confirmar que todos os batches do Pass 1 foram baixados)
"""

import csv
import os
import sys

import anthropic

from batch_utils import (
    PAIRS_CSV,
    INTERIM_DIR,
    MODEL_PRIMARY,
    BORDERLINE_LABELS,
    custom_id,
    load_prompt,
    make_request,
    load_state,
    save_state,
    submit_batches,
    load_jsonl_results,
)


def main() -> None:
    state = load_state()

    if any(b["pass"] == 2 for b in state["batches"]):
        print("Batches do Pass 2 já existem. Aborting.")
        return

    p1_batches = [b for b in state["batches"] if b["pass"] == 1]
    if not p1_batches or any(b["status"] != "downloaded" for b in p1_batches):
        print("Nem todos os batches do Pass 1 foram baixados. Rode 2_poll.py primeiro.")
        return

    print("Carregando resultados do Pass 1…")
    pass1 = load_jsonl_results(1)

    # Salva CSV intermediário com scores do Pass 1
    rows = list(csv.DictReader(PAIRS_CSV.open(encoding="utf-8")))
    interim = INTERIM_DIR / "pass1_scores.csv"
    with interim.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "codigo_a", "ano_a", "habilidade_a",
            "codigo_b", "ano_b", "habilidade_b",
            "pass1_label", "pass1_score", "pass1_reasoning",
        ])
        writer.writeheader()
        for row in rows:
            cid = custom_id(row)
            res = pass1.get(cid, {})
            writer.writerow({
                **row,
                "pass1_label":     res.get("label", "ERROR"),
                "pass1_score":     res.get("score", ""),
                "pass1_reasoning": res.get("reasoning", ""),
            })
    print(f"Pass 1 scores salvos → {interim}")

    # Filtra borderlines para o Pass 2
    borderline_rows = [
        row for row in rows
        if pass1.get(custom_id(row), {}).get("label")
        in BORDERLINE_LABELS
    ]
    print(f"Pares borderline para Pass 2: {len(borderline_rows):,}")

    if not borderline_rows:
        print("Nenhum borderline encontrado. Pule para 5_assemble.py.")
        return

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    system, user_tpl = load_prompt()

    requests = [
        make_request(row, system, user_tpl, MODEL_PRIMARY, temperature=0.5)
        for row in borderline_rows
    ]

    n = submit_batches(client, requests, pass_n=2, state=state)
    print(f"\n{n} batch(es) submetido(s). Rode 2_poll.py novamente.")


if __name__ == "__main__":
    main()
