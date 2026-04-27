"""
Pós-processamento de consistência para pares simétricos (delta=0).

Quando ano_a == ano_b, tanto (A,B) quanto (B,A) estão no dataset e foram
julgados de forma independente. Isso pode gerar inconsistências: se o modelo
classifica A→B como pré-requisito E B→A como pré-requisito, temos uma
dependência circular — o que é pedagogicamente impossível.

Resolução em dois níveis:

  1. Pass Simétrico (preferido): se submit_pass_sym.py foi rodado e o Sonnet
     avaliou ambas as direções num único request, esse score é usado diretamente.
     O modelo recebe a restrição score(A→B) + score(B→A) ≤ 1.0 mas pode atribuir
     valores baixos a ambas (habilidades de domínios distintos).

  2. Correção algébrica (fallback): pares inconsistentes sem score simétrico
     recebem a correção:
       score_corrigido(A→B) = ( raw(A→B) + (1 − raw(B→A)) ) / 2
     Propriedade: score_corrigido(A→B) + score_corrigido(B→A) = 1.0.
     Pares consistentes (soma ≤ 1.0) não são alterados.

Colunas adicionadas ao output:
  consistency_flag:
    "sym_scored"              par resolvido pelo Pass Simétrico (Sonnet, dois sentidos)
    "symmetric_corrected"     par inconsistente sem sym → correção algébrica aplicada
    "symmetric_consistent"    par delta=0 consistente (soma ≤ 1.0) → score intacto
    "symmetric_pair_missing"  par delta=0 sem simétrico com score utilizável
    "no_symmetric_possible"   par delta>0 → nenhum simétrico existe no dataset
    "source_missing"          par sem score do pipeline anterior

Output: data/prereq_pairs_final.csv

Uso:
    python scripts/consistency.py
    (somente após assemble.py; opcionalmente após poll.py confirmar pass sym)
"""

import csv
import sys
from collections import Counter
from pathlib import Path

from batch_utils import load_jsonl_results_sym, load_state

INPUT  = Path(__file__).parent.parent / "data" / "prereq_pairs_scored.csv"
OUTPUT = Path(__file__).parent.parent / "data" / "prereq_pairs_final.csv"


def main() -> None:
    if not INPUT.exists():
        print(f"Arquivo de entrada não encontrado: {INPUT}")
        print("Rode assemble.py primeiro.")
        return

    # Carrega resultados do pass simétrico, se disponíveis
    state = load_state()
    has_sym = any(b["pass"] == "sym" and b["status"] == "downloaded" for b in state["batches"])
    if has_sym:
        print("Carregando resultados do Pass Simétrico…")
        sym_results = load_jsonl_results_sym()
    else:
        sym_results = {}
        print("Pass Simétrico não encontrado — usando correção algébrica para inconsistências.")

    rows = list(csv.DictReader(INPUT.open(encoding="utf-8")))

    # Indexa scores brutos por (codigo_a, codigo_b)
    raw_scores: dict[tuple, float | None] = {}
    for row in rows:
        key = (row["codigo_a"], row["codigo_b"])
        try:
            raw_scores[key] = float(row["score"])
        except (ValueError, TypeError):
            raw_scores[key] = None

    flag_counts: Counter = Counter()

    with OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "codigo_a", "ano_a", "habilidade_a",
            "codigo_b", "ano_b", "habilidade_b",
            "score", "source", "consistency_flag",
        ])
        writer.writeheader()

        for row in rows:
            ca, cb = row["codigo_a"], row["codigo_b"]
            key_ab = (ca, cb)
            key_ba = (cb, ca)
            cid_ab = f"{ca}__{cb}"
            raw_ab = raw_scores.get(key_ab)

            if raw_ab is None:
                flag = "source_missing"
                writer.writerow({**row, "consistency_flag": flag})
                flag_counts[flag] += 1
                continue

            if int(row["ano_a"]) != int(row["ano_b"]):
                flag = "no_symmetric_possible"
                writer.writerow({**row, "score": raw_ab, "consistency_flag": flag})
                flag_counts[flag] += 1
                continue

            raw_ba = raw_scores.get(key_ba)
            if raw_ba is None:
                flag = "symmetric_pair_missing"
                writer.writerow({**row, "score": raw_ab, "consistency_flag": flag})
                flag_counts[flag] += 1
                continue

            if raw_ab + raw_ba <= 1.0:
                flag = "symmetric_consistent"
                writer.writerow({**row, "score": raw_ab, "consistency_flag": flag})
                flag_counts[flag] += 1
                continue

            # Par inconsistente: usa sym se disponível, senão aplica fórmula.
            if cid_ab in sym_results:
                sym = sym_results[cid_ab]
                flag = "sym_scored"
                writer.writerow({
                    **row,
                    "score":  sym["score"],
                    "source": sym["source"],
                    "consistency_flag": flag,
                })
            else:
                corrected = round((raw_ab + (1.0 - raw_ba)) / 2, 4)
                flag = "symmetric_corrected"
                writer.writerow({**row, "score": corrected, "consistency_flag": flag})
            flag_counts[flag] += 1

    print(f"\nCSV final salvo → {OUTPUT} ({len(rows):,} pares)\n")
    print("Distribuição de consistency_flag:")
    for flag, n in sorted(flag_counts.items(), key=lambda x: -x[1]):
        print(f"  {flag:<28s} {n:>7,}")


if __name__ == "__main__":
    main()
