# BNCC Prereq Graph: Matemática

> Dado um currículo de 290 habilidades de Matemática, qual vem antes de qual?

Este projeto constrói um **grafo de pré-requisitos** para todas as habilidades de Matemática da BNCC (EF anos 1–9 + EM) usando LLMs em larga escala como juízes pedagógicos. O output é uma tabela de ~22 mil pares com um score de 0–1 indicando a probabilidade de A ser pré-requisito direto de B.

---

## Por que isso é útil?

A BNCC define *o que* ensinar, mas não *em que ordem* dentro de um mesmo ano ou entre anos próximos. Esse grafo torna explícita a estrutura de dependência latente no currículo, útil para:

- **Sequenciamento adaptativo**: recomendar a próxima habilidade que um aluno está pronto para aprender
- **Diagnóstico de lacunas**: identificar pré-requisitos não dominados quando um aluno trava em determinado conteúdo
- **Análise curricular**: detectar habilidades sem predecessores claros ou com dependências circulares entre anos

---

## Quickstart

```bash
# 1. Instale as dependências
pip install -e .

# 2. Smoke test com ~20 pares e chamadas síncronas (< $0,01)
export ANTHROPIC_API_KEY=sk-ant-...
python3 scripts/run_sample.py --nrows 20
```

Os dados de entrada (`data/matematica_bncc.csv` e `data/prereq_pairs_bncc.csv`) já estão no repositório. O output final (`data/prereq_pairs_final.csv`) é gerado pelo pipeline de batch abaixo.

---

## Pipeline completo

O pipeline roda em três passes, cada um construindo sobre o anterior:

```bash
python3 scripts/submit_pass1.py   # Pass 1: todos os ~22 k pares → Haiku, temp=0
python3 scripts/poll.py           # aguarda conclusão (repita até zerar)

python3 scripts/submit_pass2.py   # Pass 2: só os borderlines → Haiku, temp=0.5
python3 scripts/poll.py

python3 scripts/submit_pass3.py   # Pass 3: só os discordantes → Sonnet, temp=0
python3 scripts/poll.py

python3 scripts/assemble.py       # consolida → data/prereq_pairs_scored.csv
python3 scripts/consistency.py    # corrige simetria → data/prereq_pairs_final.csv
```

> **Dica:** `submit_pass1.py` aceita `--limit N` para testar com um subconjunto antes de rodar tudo.

---

## Arquitetura técnica

### Geração dos pares candidatos

Em vez de avaliar todos os C(290, 2) ≈ 42 k pares ordenados, `build_pairs.py` restringe a pares com **delta de ano ≤ 2**: a premissa é que pré-requisitos diretos raramente saltam mais de dois anos letivos. Isso reduz o espaço para ~22 k pares sem perder relações pedagogicamente relevantes.

### Prompt de julgamento

O LLM não produz scores numéricos diretamente. Em vez disso, ele classifica cada par em uma escala ordinal de cinco rótulos, forçando uma decisão categórica antes da conversão numérica:

| Rótulo | Score |
|--------|-------|
| `DEFINITIVAMENTE_SIM` | 1,0 |
| `PROVAVELMENTE_SIM` | 0,75 |
| `INCERTO` | 0,5 |
| `PROVAVELMENTE_NÃO` | 0,25 |
| `DEFINITIVAMENTE_NÃO` | 0,0 |

O prompt (`prompts/prereq_judge.md`) inclui seis exemplos anotados com habilidades reais da BNCC, uma definição precisa de "pré-requisito direto" (conceitualmente imediato, não apenas correlacionado), e instrui o modelo a retornar JSON `{reasoning, label}`, com o raciocínio antes do veredito para forçar cadeia de pensamento.

### Estratégia de três passes

O design equilibra custo e qualidade:

| Pass | Modelo | Temp | Escopo | Propósito |
|------|--------|------|--------|-----------|
| 1 | Haiku 4.5 | 0 | Todos os ~22 k pares | Triagem determinística barata |
| 2 | Haiku 4.5 | 0,5 | Pares com label `PROVAVELMENTE_*` ou `INCERTO` | Segunda opinião com temperatura para os casos ambíguos |
| 3 | Sonnet 4.6 | 0 | Pares onde \|score₁ − score₂\| > 0,25 | Árbitro de maior capacidade para discordâncias reais |

**Resolução por `decide()`:**

```
se Pass 1 é DEFINITIVAMENTE_* → usa score do Pass 1 direto
se borderline e |s1 − s2| ≤ 0.25 → média(s1, s2)
se borderline e |s1 − s2| > 0.25 → usa Pass 3 (ou média como fallback)
```

O campo `source` no CSV final documenta o caminho percorrido por cada par: `pass1`, `pass1+pass2_avg`, `pass3`, ou `pass1+pass2_fallback`.

### Correção de simetria

Pares do mesmo ano letivo (delta = 0) são avaliados nas duas direções: A→B e B→A. Se `score(A→B) + score(B→A) > 1,0`, existe um sinal de dependência circular: o modelo diz que A precede B *e* B precede A com mais confiança do que é logicamente possível.

A correção garante `score(A→B) + score(B→A) = 1,0`:

```
corrected(A→B) = ( raw(A→B) + (1 − raw(B→A)) ) / 2
corrected(B→A) = ( raw(B→A) + (1 − raw(A→B)) ) / 2
```

O CSV final inclui o campo `consistency_flag` documentando o resultado para cada par:

| Flag | Significado |
|------|-------------|
| `symmetric_corrected` | delta=0, simétrico existe, `raw_ab + raw_ba > 1,0` → correção aplicada |
| `symmetric_consistent` | delta=0, simétrico existe, `raw_ab + raw_ba ≤ 1,0` → score mantido |
| `symmetric_pair_missing` | delta=0, mas o par simétrico não tem score utilizável |
| `no_symmetric_possible` | delta > 0 → não existe par simétrico no dataset |
| `source_missing` | par sem score do pipeline anterior |

### Uso da Message Batches API

Todos os passes usam a [Message Batches API](https://docs.anthropic.com/en/api/creating-message-batches) da Anthropic, que oferece **50% de desconto** em relação às chamadas síncronas com SLA de 24h. O system prompt é marcado com `cache_control: ephemeral` em todas as requisições, reduzindo custo e latência nas janelas de cache. O estado entre passes é persistido em `data/batch_state.json` (gitignored), e os resultados brutos em JSONL ficam em `data/raw_results/` (também gitignored).

---

## Estrutura do repositório

```
data/
  matematica_bncc.csv          290 habilidades-fonte (EF + EM)
  prereq_pairs_bncc.csv        ~22 k pares candidatos (delta ≤ 2 anos)
  prereq_pairs_final.csv       output final (gerado pelo pipeline)
prompts/
  prereq_judge.md              prompt completo do juiz (system + user template + exemplos)
scripts/
  fetch_skills.py              coleta habilidades da API pública
  build_pairs.py               gera pares por proximidade de ano
  batch_utils.py               biblioteca compartilhada (instalada como pacote)
  submit_pass{1,2,3}.py        submissão de cada pass para a Batch API
  poll.py                      polling e download de resultados
  assemble.py                  consolida os três passes → prereq_pairs_scored.csv
  consistency.py               correção de simetria → prereq_pairs_final.csv
  run_sample.py                smoke test síncrono com amostragem estratificada e 3 passes completos
tests/
  test_core.py                 testes das funções puras (sem chamadas de API)
```

---

## Fonte dos dados

Habilidades coletadas da [API não-oficial Cientificar 1992](https://cientificar1992.pythonanywhere.com/). O PDF oficial da BNCC está disponível em `data/raw/BNCC_EI_EF.pdf` como referência. Para regenerar os dados de entrada do zero:

```bash
python3 scripts/fetch_skills.py   # → data/matematica_bncc.csv
python3 scripts/build_pairs.py    # → data/prereq_pairs_bncc.csv
```
