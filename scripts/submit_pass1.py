"""
Pass 1 — submete todos os pares ao Haiku com temperature=0 (determinístico).

Divide os 22 k pares em batches de até 10 000 e submete cada um à
Message Batches API (50 % de desconto vs. chamadas em tempo real).
Os batch IDs ficam registrados em data/batch_state.json.

Uso:
    python scripts/submit_pass1.py            # todos os pares
    python scripts/submit_pass1.py --limit 20 # teste com 20 pares
"""

import argparse
import csv
import os
import sys

import anthropic

from batch_utils import (
    PAIRS_CSV,
    MODEL_PRIMARY,
    load_prompt,
    make_request,
    load_state,
    submit_batches,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                        help="Submete apenas os primeiros N pares (útil para testes)")
    args = parser.parse_args()

    state = load_state()

    if any(b["pass"] == 1 for b in state["batches"]):
        print("Batches do Pass 1 já existem em data/batch_state.json.")
        print("Remova as entradas de pass=1 do arquivo para resubmeter.")
        return

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    system, user_tpl = load_prompt()

    rows = list(csv.DictReader(PAIRS_CSV.open(encoding="utf-8")))
    if args.limit:
        rows = rows[: args.limit]
        print(f"Modo teste: usando {len(rows)} pares de {PAIRS_CSV.name}")
    else:
        print(f"Pares carregados: {len(rows):,}")

    requests = [
        make_request(row, system, user_tpl, MODEL_PRIMARY, temperature=0)
        for row in rows
    ]

    n = submit_batches(client, requests, pass_n=1, state=state)
    print(f"\n{n} batch(es) submetido(s). Rode poll.py para acompanhar.")


if __name__ == "__main__":
    main()
