# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (required before running scripts or tests)
pip install -e .

# Tests
python3 -m pytest tests/ -v
python3 -m pytest tests/test_core.py::TestDecide -v   # single class
python3 -m pytest tests/ -k "test_valid"              # single test by name

# Smoke test the full pipeline logic with real API calls (sync, ~10 pairs, < $0.01)
python3 scripts/run_sample.py --nrows 20

# Regenerate source data (rarely needed — outputs already committed)
python3 scripts/fetch_skills.py    # → data/matematica_bncc.csv
python3 scripts/build_pairs.py     # → data/prereq_pairs_bncc.csv
```

## Batch pipeline (requires `ANTHROPIC_API_KEY`)

Run scripts in order; `poll.py` must confirm each pass is downloaded before proceeding:

```
submit_pass1.py [--limit N]   # Pass 1: all pairs → Haiku, temp=0
poll.py                        # repeat until no batches in_progress
submit_pass2.py                # Pass 2: borderlines → Haiku, temp=0.5
poll.py
submit_pass3.py                # Pass 3: disagreements → Sonnet, temp=0
poll.py
assemble.py                    # → data/prereq_pairs_scored.csv
submit_pass_sym.py             # Pass sym: delta=0 inconsistencies → Sonnet, both directions at once
poll.py
consistency.py                 # → data/prereq_pairs_final.csv
```

State between passes is persisted in `data/batch_state.json` (gitignored). Raw JSONL results land in `data/raw_results/` (also gitignored). Intermediate per-pass CSVs go to `data/interim/`.

## Architecture

### Data lineage

```
fetch_skills.py
  └→ data/matematica_bncc.csv          (290 math skills, EF yr 1–9 + EM yr 10)
       └→ build_pairs.py
            └→ data/prereq_pairs_bncc.csv    (22 k candidate pairs, delta 0–2 yrs)
                 └→ [batch pipeline]
                      └→ prereq_pairs_scored.csv
                           └→ consistency.py
                                └→ prereq_pairs_final.csv   (final output)
```

### `batch_utils.py` — shared library

Installed as an editable package (`pip install -e .`), so it's importable by both scripts and tests without path manipulation. Contains:
- Constants: model names (`MODEL_PRIMARY`, `MODEL_SECONDARY`, `MODEL_SYM`), `LABEL_TO_SCORE`, `BORDERLINE_LABELS`, `ESCALATION_THRESHOLD`, `BATCH_SIZE`
- Path definitions: `PAIRS_CSV`, `STATE_FILE`, `RAW_DIR`, `INTERIM_DIR`, `PROMPT_FILE`, `PROMPT_FILE_SYM`
- `load_prompt(path)` / `fill_user()` — parses a prompt `.md` file and substitutes `{{...}}` variables; accepts optional `path` so both `prereq_judge.md` and `prereq_judge_sym.md` can be loaded; `fill_user()` is single-pass (regex) so substituted values are never re-processed
- `custom_id(row)` — returns `{codigo_a}__{codigo_b}`; stable key used to correlate results across all passes
- `sym_custom_id(row)` — returns `{menor}__{maior}__sym`; canonical ID for symmetric pass requests (smaller code first)
- `make_request(row, system, user_template, model, temperature, context_note)` — builds a complete Batch API request dict; system prompt is wrapped in a `cache_control: ephemeral` block for prompt caching; `context_note` prepends prior-pass context for Pass 3 escalations
- `make_request_sym(row, system, user_template, model, temperature)` — same as `make_request` but uses `sym_custom_id`; row must have `codigo_a < codigo_b` (caller's responsibility)
- `submit_batches(client, requests, pass_n, state)` — chunks requests into `BATCH_SIZE` slices, submits each to the Batch API, and persists state; `pass_n` can be int (1–3) or `"sym"`
- `parse_response(text)` — extracts `{reasoning, label}` JSON via bracket matching; returns the **last** valid object in the text; derives `score` from `LABEL_TO_SCORE`
- `parse_sym_response(text)` — extracts `{reasoning, label_ab, label_ba}` from a symmetric response; derives `score_ab` and `score_ba`; warns (but still returns) if `score_ab + score_ba > 1.0`
- `load_jsonl_results(pass_n)` — reads all downloaded JSONL files for a given pass; prints a count of valid vs. error/unparseable results with a warning if the error rate exceeds 5%
- `load_jsonl_results_sym()` — reads sym-pass JSONL; expands each result into two directional entries (`A__B` and `B__A`)
- `load_state()` / `save_state(state)` — read/write `data/batch_state.json`; `load_state()` raises `ValueError` on malformed files (wrong type or missing `batches` key)
- `decide(cid, pass1, pass2, pass3)` — three-pass resolution logic returning `(score, source)`
- `apply_consistency(rows, results)` — symmetry correction for delta=0 pairs (used internally by legacy path)

### Three-pass scoring logic

| Condition | Resolution |
|---|---|
| Pass 1 label is DEFINITIVAMENTE_* | Use Pass 1 score directly |
| Borderline, `\|s1−s2\| ≤ 0.25` | Average of Pass 1 + Pass 2 |
| Borderline, `\|s1−s2\| > 0.25` | Escalate to Sonnet (Pass 3) |
| Pass 3 unavailable | Average Pass 1+2 as fallback |

Scores from averaging are non-canonical floats (e.g. `0.625`) — they are final values and are never converted back to labels.

### Symmetry resolution

For delta=0 pairs where `raw(A→B) + raw(B→A) > 1.0` (circular dependency signal), two resolution paths exist:

**Pass Simétrico (preferred):** `submit_pass_sym.py` sends both skills in a single Sonnet request using `prompts/prereq_judge_sym.md`. The model is told that `score(A→B) + score(B→A) ≤ 1.0` but can assign both low (unlike the algebraic formula, which forces the sum to exactly 1.0). Result is stored with `source="sym"`.

**Correção algébrica (fallback):** used when the sym pass was not run or a pair has no sym result:
```
corrected(A→B) = ( raw(A→B) + (1 − raw(B→A)) ) / 2
```
This guarantees `corrected(A→B) + corrected(B→A) = 1.0`.

The `consistency_flag` column in the final CSV records the outcome for every row:

| Flag | Meaning |
|---|---|
| `sym_scored` | delta=0, inconsistent, resolved by sym pass → Sonnet scored both directions |
| `symmetric_corrected` | delta=0, inconsistent, no sym result → algebraic correction applied |
| `symmetric_consistent` | delta=0, partner exists, `raw_ab + raw_ba ≤ 1.0` → score unchanged |
| `symmetric_pair_missing` | delta=0, but partner row has no usable score |
| `no_symmetric_possible` | delta > 0 → no symmetric pair exists in the dataset |
| `source_missing` | pair has no score from the pipeline |

### Prompt format

`prompts/prereq_judge.md` contains two fenced code blocks (SYSTEM and USER) parsed by `load_prompt()`. The USER block has six `{{PLACEHOLDER}}` variables filled by `fill_user()`. Do not change the fence markers or block order without updating the regex in `load_prompt()`.

### Tests

`tests/conftest.py` adds `scripts/` to `sys.path` so `build_pairs` is importable alongside the installed `batch_utils`. Tests cover only pure functions — no API calls, no file I/O. Coverage includes: `parse_response`, `parse_sym_response`, `fill_user`, `decide`, `candidate_pairs`, `apply_consistency`, `custom_id`, `sym_custom_id`, `make_request`, `make_request_sym`, `load_prompt` (both prompts), `load_state`, `save_state`.
