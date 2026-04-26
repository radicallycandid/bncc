# BNCC Prereq Graph — Matemática

Constrói um grafo de pré-requisitos entre as **290 habilidades de Matemática da BNCC** (EF anos 1–9 + EM) usando julgamento de LLM em larga escala.

## O que este projeto produz

`data/prereq_pairs_final.csv` — ~22 mil pares `(A, B)` com um score de 0 a 1 indicando a probabilidade de A ser pré-requisito direto de B. Score ≥ 0.75 indica relação forte; ≤ 0.25 indica ausência de dependência.

## Como funciona

### 1. Coleta de dados

```bash
python3 scripts/fetch_skills.py   # baixa habilidades da API pública
python3 scripts/build_pairs.py    # gera pares candidatos (delta de 0–2 anos)
```

Os outputs (`data/matematica_bncc.csv` e `data/prereq_pairs_bncc.csv`) já estão commitados — só rode esses scripts se quiser regenerar.

### 2. Scoring via Batch API

Requer `export ANTHROPIC_API_KEY=sk-ant-...`.

```bash
pip install -e .

# Teste rápido antes de rodar tudo (~20 pares, chamadas síncronas, < $0.01)
python3 scripts/run_sample.py --nrows 20

# Pipeline completo (~22 k pares, usa Message Batches API com 50% de desconto)
python3 scripts/submit_pass1.py
python3 scripts/poll.py            # repita até não haver batches em andamento
python3 scripts/submit_pass2.py
python3 scripts/poll.py
python3 scripts/submit_pass3.py    # só necessário se houver escalações
python3 scripts/poll.py
python3 scripts/assemble.py        # → data/prereq_pairs_scored.csv
python3 scripts/consistency.py     # → data/prereq_pairs_final.csv
```

### Estratégia de scoring (3 passes)

| Pass | Modelo | Temp | Escopo |
|------|--------|------|--------|
| 1 | Haiku 4.5 | 0 | Todos os 22 k pares |
| 2 | Haiku 4.5 | 0.5 | Apenas labels borderline (PROVAVELMENTE_*, INCERTO) |
| 3 | Sonnet 4.6 | 0 | Pares onde Pass 1 e Pass 2 discordaram em > 0.25 |

Pares onde Pass 1 e 2 concordam são resolvidos por média. Pares do mesmo ano letivo passam por correção de simetria para eliminar dependências circulares.

## Estrutura

```
data/
  matematica_bncc.csv        habilidades-fonte (290 linhas)
  prereq_pairs_bncc.csv      pares candidatos (~22 k linhas)
  prereq_pairs_final.csv     output final (gerado pelo pipeline)
prompts/
  prereq_judge.md            prompt do juiz (system + user template)
scripts/
  fetch_skills.py            coleta habilidades da API
  build_pairs.py             gera pares por proximidade de ano
  batch_utils.py             biblioteca compartilhada (instalada como pacote)
  submit_pass{1,2,3}.py      submissão de cada pass
  poll.py                    polling e download de resultados
  assemble.py                consolida os três passes
  consistency.py             correção de simetria
  run_sample.py              smoke test síncrono
tests/
  test_core.py               testes das funções puras do pipeline
```

## Fonte dos dados

Habilidades coletadas da [API não-oficial Cientificar 1992](https://cientificar1992.pythonanywhere.com/). PDF oficial da BNCC disponível em `data/raw/BNCC_EI_EF.pdf` como referência.
