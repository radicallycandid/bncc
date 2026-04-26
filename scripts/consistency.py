"""
Pós-processamento de consistência para pares simétricos (delta=0).

Quando ano_a == ano_b, tanto (A,B) quanto (B,A) estão no dataset e foram
julgados de forma independente. Isso pode gerar inconsistências: se o modelo
classifica A→B como pré-requisito E B→A como pré-requisito, temos uma
dependência circular — o que é logicamente suspeito.

Correção aplicada a TODOS os pares simétricos (não apenas inconsistentes):

    score_corrigido(A→B) = ( raw(A→B) + (1 − raw(B→A)) ) / 2
    score_corrigido(B→A) = ( raw(B→A) + (1 − raw(A→B)) ) / 2

Propriedade garantida: score_corrigido(A→B) + score_corrigido(B→A) = 1.0

Interpretação:
  - Se o modelo foi consistente (raw(A→B)=0.75, raw(B→A)=0.25):
      corrected(A→B) = (0.75 + 0.75) / 2 = 0.75  ← sem mudança significativa
  - Se o modelo foi inconsistente (raw(A→B)=0.75, raw(B→A)=0.75):
      corrected(A→B) = (0.75 + 0.25) / 2 = 0.50  ← INCERTO (modelo não sabe a direção)
  - Se ambos são baixos (raw(A→B)=0.25, raw(B→A)=0.25):
      corrected(A→B) = (0.25 + 0.75) / 2 = 0.50  ← idem

Colunas adicionadas ao output:
  consistency_flag:
    "symmetric_corrected"  par delta=0 com simétrico encontrado → correção aplicada
    "no_symmetric_pair"    par delta>0 → sem correção (impossível ter simétrico no dataset)
    "source_missing"       par sem score do pipeline anterior

Output: data/prereq_pairs_final.csv

Uso:
    python scripts/6_consistency.py
    (somente após 5_assemble.py ter gerado data/prereq_pairs_scored.csv)
"""

import csv
import sys
from collections import Counter
from pathlib import Path

INPUT  = Path(__file__).parent.parent / "data" / "prereq_pairs_scored.csv"
OUTPUT = Path(__file__).parent.parent / "data" / "prereq_pairs_final.csv"


def main() -> None:
    if not INPUT.exists():
        print(f"Arquivo de entrada não encontrado: {INPUT}")
        print("Rode 5_assemble.py primeiro.")
        return

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
            key_ab = (row["codigo_a"], row["codigo_b"])
            key_ba = (row["codigo_b"], row["codigo_a"])
            raw_ab = raw_scores.get(key_ab)

            if raw_ab is None:
                flag = "source_missing"
                writer.writerow({**row, "consistency_flag": flag})
                flag_counts[flag] += 1
                continue

            if int(row["ano_a"]) != int(row["ano_b"]):
                flag = "no_symmetric_pair"
                writer.writerow({**row, "score": raw_ab, "consistency_flag": flag})
                flag_counts[flag] += 1
                continue

            raw_ba = raw_scores.get(key_ba)
            if raw_ba is None or raw_ab + raw_ba <= 1.0:
                # Sem simétrico ou sem inconsistência real — mantém score intacto
                flag = "no_symmetric_pair"
                writer.writerow({**row, "score": raw_ab, "consistency_flag": flag})
                flag_counts[flag] += 1
                continue

            # Inconsistência real (ambos altos): aplica correção de simetria.
            # O resultado pode ser um float não-canônico (ex.: 0.625) — é o valor
            # final e não precisa ser convertido de volta para um label de palavras.
            corrected = round((raw_ab + (1.0 - raw_ba)) / 2, 4)
            flag = "symmetric_corrected"
            writer.writerow({**row, "score": corrected, "consistency_flag": flag})
            flag_counts[flag] += 1

    print(f"CSV final salvo → {OUTPUT} ({len(rows):,} pares)\n")
    print("Distribuição de consistency_flag:")
    for flag, n in sorted(flag_counts.items(), key=lambda x: -x[1]):
        print(f"  {flag:<25s} {n:>7,}")

    # Verifica propriedade de soma = 1 em amostra de pares simétricos
    corrected_rows = {
        (r["codigo_a"], r["codigo_b"]): float(r["score"])
        for r in csv.DictReader(OUTPUT.open(encoding="utf-8"))
        if r["consistency_flag"] == "symmetric_corrected"
    }
    violations = 0
    checked = 0
    seen: set = set()
    for (a, b), s_ab in corrected_rows.items():
        if (b, a) in corrected_rows and (a, b) not in seen:
            s_ba = corrected_rows[(b, a)]
            if abs(s_ab + s_ba - 1.0) > 0.001:
                violations += 1
            seen.add((a, b))
            seen.add((b, a))
            checked += 1

    print(f"\nVerificação de simetria: {checked:,} pares checados, {violations} violações.")
    if violations == 0:
        print("✓ score(A→B) + score(B→A) = 1.0 para todos os pares simétricos.")


if __name__ == "__main__":
    main()
