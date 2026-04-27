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
- Constants: model names, `LABEL_TO_SCORE`, `BORDERLINE_LABELS`, `ESCALATION_THRESHOLD`, `BATCH_SIZE`
- Path definitions: `PAIRS_CSV`, `STATE_FILE`, `RAW_DIR`, `INTERIM_DIR`, `PROMPT_FILE`
- `load_prompt()` / `fill_user()` — parses `prompts/prereq_judge.md` and substitutes `{{...}}` variables; `fill_user()` is single-pass (regex) so substituted values are never re-processed
- `custom_id(row)` — returns `{codigo_a}__{codigo_b}`; stable key used to correlate results across all passes
- `make_request(row, system, user_template, model, temperature, context_note)` — builds a complete Batch API request dict; system prompt is wrapped in a `cache_control: ephemeral` block for prompt caching; `context_note` prepends prior-pass context for Pass 3 escalations
- `submit_batches(client, requests, pass_n, state)` — chunks requests into `BATCH_SIZE` slices, submits each to the Batch API, and persists state
- `parse_response(text)` — extracts `{reasoning, label}` JSON via bracket matching; returns the **last** valid object in the text (handles model self-corrections where two JSON blocks appear); derives `score` from `LABEL_TO_SCORE`
- `load_jsonl_results(pass_n)` — reads all downloaded JSONL files for a given pass; prints a count of valid vs. error/unparseable results with a warning if the error rate exceeds 5%
- `load_state()` / `save_state(state)` — read/write `data/batch_state.json`; `load_state()` raises `ValueError` on malformed files (wrong type or missing `batches` key)
- `decide(cid, pass1, pass2, pass3)` — three-pass resolution logic returning `(score, source)`
- `apply_consistency(rows, results)` — symmetry correction for delta=0 pairs

### Three-pass scoring logic

| Condition | Resolution |
|---|---|
| Pass 1 label is DEFINITIVAMENTE_* | Use Pass 1 score directly |
| Borderline, `\|s1−s2\| ≤ 0.25` | Average of Pass 1 + Pass 2 |
| Borderline, `\|s1−s2\| > 0.25` | Escalate to Sonnet (Pass 3) |
| Pass 3 unavailable | Average Pass 1+2 as fallback |

Scores from averaging are non-canonical floats (e.g. `0.625`) — they are final values and are never converted back to labels.

### Symmetry correction

For delta=0 pairs where `raw(A→B) + raw(B→A) > 1.0` (circular dependency signal):

```
corrected(A→B) = ( raw(A→B) + (1 − raw(B→A)) ) / 2
```

This guarantees `corrected(A→B) + corrected(B→A) = 1.0`.

The `consistency_flag` column in the final CSV records the outcome for every row:

| Flag | Meaning |
|---|---|
| `symmetric_corrected` | delta=0, partner exists, `raw_ab + raw_ba > 1.0` → correction applied |
| `symmetric_consistent` | delta=0, partner exists, `raw_ab + raw_ba ≤ 1.0` → score unchanged |
| `symmetric_pair_missing` | delta=0, but partner row has no usable score |
| `no_symmetric_possible` | delta > 0 → no symmetric pair exists in the dataset |
| `source_missing` | pair has no score from the pipeline |

### Prompt format

`prompts/prereq_judge.md` contains two fenced code blocks (SYSTEM and USER) parsed by `load_prompt()`. The USER block has six `{{PLACEHOLDER}}` variables filled by `fill_user()`. Do not change the fence markers or block order without updating the regex in `load_prompt()`.

### Tests

`tests/conftest.py` adds `scripts/` to `sys.path` so `build_pairs` is importable alongside the installed `batch_utils`. Tests cover only pure functions — no API calls, no file I/O. Coverage includes: `parse_response`, `fill_user`, `decide`, `candidate_pairs`, `apply_consistency`, `custom_id`, `make_request`, `load_prompt`, `load_state`, `save_state`.
