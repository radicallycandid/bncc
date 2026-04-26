"""
Consolida os resultados de todos os passes em data/prereq_pairs_scored.csv.

Regra de decisão por par:
  - Pass 1 extremo (DEFINITIVAMENTE_SIM ou DEFINITIVAMENTE_NÃO):
      → score do Pass 1 diretamente (não foi resubmetido)
  - Borderline com Pass 1 e Pass 2 concordando (|score1 - score2| ≤ 0.25):
      → média dos dois scores
  - Borderline escalado com Pass 3 bem-sucedido:
      → score do Pass 3 (Sonnet)
  - Borderline escalado com Pass 3 falhando (erro de API / unparseable):
      → média Pass 1 + Pass 2 como fallback
  - Par ausente em Pass 1 (erro de API):
      → score vazio, source="missing"

Colunas do output:
  codigo_a, ano_a, habilidade_a, codigo_b, ano_b, habilidade_b,
  score, label, source

'source' indica qual caminho determinou o score:
  pass1              extremos decididos pelo primeiro julgamento
  pass1+pass2_avg    borderlines onde os dois Haiku concordaram
  pass3              escalações decididas pelo Sonnet
  pass1+pass2_fallback  escalação onde o Sonnet também falhou
  missing            par sem resultado utilizável

Uso:
    python scripts/assemble.py
    (somente após poll.py confirmar que todos os batches ativos foram baixados)
"""

import csv
import sys
from collections import Counter
from pathlib import Path

from batch_utils import (
    PAIRS_CSV,
    custom_id,
    decide,
    load_state,
    load_jsonl_results,
)

OUTPUT = Path(__file__).parent.parent / "data" / "prereq_pairs_scored.csv"


def main() -> None:
    state = load_state()
    has_pass = {
        p: any(b["pass"] == p and b["status"] == "downloaded" for b in state["batches"])
        for p in (1, 2, 3)
    }

    if not has_pass[1]:
        print("Nenhum resultado do Pass 1 encontrado. Verifique o batch_state.json.")
        return

    print("Carregando resultados…")
    pass1 = load_jsonl_results(1)
    pass2 = load_jsonl_results(2) if has_pass[2] else {}
    pass3 = load_jsonl_results(3) if has_pass[3] else {}

    rows = list(csv.DictReader(PAIRS_CSV.open(encoding="utf-8")))
    source_counts: Counter = Counter()

    with OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "codigo_a", "ano_a", "habilidade_a",
            "codigo_b", "ano_b", "habilidade_b",
            "score", "source",
        ])
        writer.writeheader()
        for row in rows:
            cid = custom_id(row)
            score, source = decide(cid, pass1, pass2, pass3)
            source_counts[source] += 1
            writer.writerow({**row, "score": score, "source": source})

    print(f"\nCSV final salvo → {OUTPUT} ({len(rows):,} pares)")
    print("\nDistribuição por source:")
    for src, n in sorted(source_counts.items(), key=lambda x: -x[1]):
        print(f"  {src:<25s} {n:>7,}")
    print("\nDistribuição de scores (arredondados):")
    score_counts: Counter = Counter()
    for row in csv.DictReader(OUTPUT.open(encoding="utf-8")):
        try:
            score_counts[round(float(row["score"]), 2)] += 1
        except (ValueError, TypeError):
            score_counts["missing"] += 1
    for s, n in sorted(score_counts.items(), key=lambda x: (isinstance(x[0], str), x[0])):
        print(f"  {str(s):<8s} {n:>7,}")

    print("\nPróximo passo: rode consistency.py")


if __name__ == "__main__":
    main()
