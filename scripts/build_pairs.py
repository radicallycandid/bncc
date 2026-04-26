"""
Gera todos os pares candidatos (A, B) em que A pode ser pré-requisito direto de B,
com base somente na restrição de proximidade de ano letivo definida abaixo.

Critério de elegibilidade
--------------------------
Dado delta = ano_equivalente(B) - ano_equivalente(A):

  delta < 0  →  A é posterior a B, impossível ser pré-requisito  (excluído)
  delta > 2  →  A é distante demais de B, não é direto            (excluído)
  0 ≤ delta ≤ 2  →  par candidato                                 (incluído)
  A == B     →  auto-referência, sem sentido                       (excluído)

Delta = 0 (mesmo ano) é incluído porque habilidades do mesmo ano podem ter
dependência interna (ex.: uma habilidade geométrica que pressupõe outra do
mesmo ano sobre polígonos).

Abordagem
---------
n = 290 habilidades → produto cartesiano tem 290² = 84.100 pares.
É trivialmente pequeno: itertools.product em memória, sem pandas nem SQL.
O script lê o CSV gerado por fetch_skills.py e salva o resultado em
data/prereq_pairs_bncc.csv.

Colunas do output
-----------------
  codigo_a        código BNCC da habilidade A (pré-requisito candidato)
  ano_a           ano_equivalente de A
  habilidade_a    texto completo da habilidade A
  codigo_b        código BNCC da habilidade B (habilidade dependente)
  ano_b           ano_equivalente de B
  habilidade_b    texto completo da habilidade B

A ordem espelha o template do prompt em prompts/prereq_judge.md, onde todos os
seis campos são variáveis substituídas antes do envio à API.
"""

import csv
import itertools
from pathlib import Path


INPUT  = Path(__file__).parent.parent / "data" / "matematica_bncc.csv"
OUTPUT = Path(__file__).parent.parent / "data" / "prereq_pairs_bncc.csv"

DELTA_MIN = 0
DELTA_MAX = 2


def load_skills(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return [
            {**row, "ano_equivalente": int(row["ano_equivalente"])}
            for row in csv.DictReader(f)
        ]


def candidate_pairs(skills: list[dict]) -> tuple[list[tuple], dict]:
    """
    Retorna os pares ordenados (a, b) onde a ≠ b e DELTA_MIN ≤ delta ≤ DELTA_MAX,
    e um Counter de distribuição por delta (para log — delta não é salvo no CSV).
    O loop duplo sobre 290 elementos executa em < 1 ms.
    """
    pairs = []
    dist: dict[int, int] = {}
    for a, b in itertools.product(skills, repeat=2):
        if a["codigo"] == b["codigo"]:
            continue
        delta = b["ano_equivalente"] - a["ano_equivalente"]
        if DELTA_MIN <= delta <= DELTA_MAX:
            pairs.append((
                a["codigo"], a["ano_equivalente"],
                a["habilidade"],
                b["codigo"], b["ano_equivalente"],
                b["habilidade"],
            ))
            dist[delta] = dist.get(delta, 0) + 1
    return pairs, dist


def main() -> None:
    skills = load_skills(INPUT)
    print(f"Habilidades carregadas: {len(skills)}")

    pairs, dist = candidate_pairs(skills)
    print(f"Pares candidatos gerados: {len(pairs)}")

    with OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["codigo_a", "ano_a", "habilidade_a", "codigo_b", "ano_b", "habilidade_b"])
        writer.writerows(pairs)

    print(f"Salvo em {OUTPUT}")
    for d in sorted(dist):
        print(f"  delta={d}: {dist[d]:,} pares")


if __name__ == "__main__":
    main()
