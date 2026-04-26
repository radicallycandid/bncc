"""
Coleta todas as habilidades de Matemática da BNCC (EF anos 1–9 e EM)
e salva em data/matematica_bncc.csv com três colunas:

    codigo          | ano_equivalente | habilidade
    EF01MA01        | 1               | Utilizar números naturais...
    ...             | ...             | ...
    EM13MAT101      | 10              | Interpretar criticamente...

Fonte dos dados
---------------
API não-oficial mantida pelo projeto Cientificar 1992 em PythonAnywhere:
  https://cientificar1992.pythonanywhere.com/visualizarBncc/

Endpoints usados:
  /bncc_fundamental/disciplina/matematica/   → EF (anos 1–9)
  /bncc_medio/disciplina/matematica_medio/   → EM

Por que essa API e não o PDF?
  O PDF oficial da BNCC tem 600 páginas e não é estruturado em tabelas
  fáceis de extrair. Essa API já entrega o dado parseado como JSON, o que
  elimina qualquer etapa de OCR/parsing e reduz o script a dois requests HTTP.

Coluna ano_equivalente
----------------------
Para o EF, o ano letivo equivalente é extraído diretamente do código da
habilidade: os dois dígitos após "EF" indicam o ano (EF01… → 1, EF09… → 9).

Para o EM, todas as habilidades da BNCC carregam o sufixo "13", que na
notação oficial significa "abrange os três anos do Ensino Médio" — não há
diferenciação por ano dentro do EM. Por convenção, atribuímos ano_equivalente
= 10, que corresponde ao 1º ano do EM na contagem contínua da educação básica
(9 anos de EF + 1º ano de EM).

Nota sobre SSL
--------------
O certificado do servidor PythonAnywhere falha na verificação do Python 3.14
(extensão Basic Constraints não marcada como crítica). A verificação é
desativada explicitamente — risco aceitável para leitura de dados públicos.
"""

import csv
import json
import re
import ssl
import urllib.request
from pathlib import Path


BASE = "https://cientificar1992.pythonanywhere.com"

ENDPOINTS = {
    "EF": f"{BASE}/bncc_fundamental/disciplina/matematica/",
    "EM": f"{BASE}/bncc_medio/disciplina/matematica_medio/",
}

# Regex que captura o código entre parênteses no início do campo nome_habilidade
# do EF, ex.: "(EF01MA01) Utilizar números naturais..." → grupo 1 = "EF01MA01"
CODE_RE = re.compile(r"^\(([A-Z0-9]+)\)\s*")

# Certificado do servidor tem Basic Constraints não marcada como crítica,
# o que o Python 3.14 rejeita; desativamos verificação para essa fonte pública.
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30, context=_SSL_CTX) as resp:
        return json.loads(resp.read().decode())


def ef_year(code: str) -> int:
    """Extrai o ano letivo do código EF (ex.: 'EF03MA07' → 3)."""
    return int(code[2:4])


def extract_ef(data: dict) -> list[tuple[str, int, str]]:
    """
    Itera sobre a hierarquia do JSON de EF:
      ano → unidades_tematicas → objeto_conhecimento → habilidades

    Retorna lista de (codigo, ano_equivalente, habilidade).
    """
    rows = []
    for ano in data.get("ano", []):
        for ut in ano.get("unidades_tematicas", []):
            for obj in ut.get("objeto_conhecimento", []):
                for hab in obj.get("habilidades", []):
                    text = hab.get("nome_habilidade", "").strip()
                    m = CODE_RE.match(text)
                    if m:
                        code = m.group(1)
                        description = CODE_RE.sub("", text).strip()
                    else:
                        code = ""
                        description = text
                    rows.append((code, ef_year(code) if code else 0, description))
    return rows


def extract_em(data: dict) -> list[tuple[str, int, str]]:
    """
    Itera sobre a hierarquia do JSON de EM:
      ano → codigo_habilidade (já tem nome_codigo e nome_habilidade separados)

    Ano equivalente fixo = 10 (1º ano do EM na contagem contínua da ed. básica).
    """
    rows = []
    for ano in data.get("ano", []):
        for hab in ano.get("codigo_habilidade", []):
            code = hab.get("nome_codigo", "").strip()
            description = hab.get("nome_habilidade", "").strip()
            rows.append((code, 10, description))
    return rows


def main() -> None:
    rows = []

    print("Fetching EF Matemática...", flush=True)
    ef_data = fetch_json(ENDPOINTS["EF"])
    ef_rows = extract_ef(ef_data)
    print(f"  -> {len(ef_rows)} habilidades EF encontradas")
    rows.extend(ef_rows)

    print("Fetching EM Matemática...", flush=True)
    em_data = fetch_json(ENDPOINTS["EM"])
    em_rows = extract_em(em_data)
    print(f"  -> {len(em_rows)} habilidades EM encontradas")
    rows.extend(em_rows)

    out_path = Path(__file__).parent.parent / "data" / "matematica_bncc.csv"
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["codigo", "ano_equivalente", "habilidade"])
        writer.writerows(rows)

    print(f"\nSalvo em {out_path} — {len(rows)} habilidades no total")

    print("\nExemplos EF:")
    for r in ef_rows[:3]:
        print(f"  {r[0]:15s} ano={r[1]}  {r[2][:70]}...")
    print("\nExemplos EM:")
    for r in em_rows[:3]:
        print(f"  {r[0]:15s} ano={r[1]}  {r[2][:70]}...")


if __name__ == "__main__":
    main()
