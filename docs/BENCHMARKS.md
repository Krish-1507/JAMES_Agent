# Benchmarks

JAMES publishes its LLM-agent benchmark results here. Every number is
reproducible with the commands below; the harness and the official scorers are
the same code that runs in CI (`evaluation/`).

## Methodology

- **GAIA** — a benchmark of real-world assistant tasks (search, spreadsheet
  manipulation, file extraction, multimodal reasoning) scored with **quasi-
  exact match**: the model's final reply must match the ground truth after
  normalization (lowercasing, article/stopword stripping, number/percent
  parsing). There is no partial credit and no multiple-choice recovery, so a
  well-calibrated, terse final answer matters as much as the reasoning steps.
- Eval runs are pinned to a single model: the worker disables provider
  failover, so every task in a run is served by the configured primary.
- Scores are computed with a faithful port of the official GAIA scorer in
  `james/evaluation/gaia.py`, not a reimplementation in the evaluation loop.
- Each task runs in an **isolated subprocess** with a hard timeout (no
  time-travel attribution, no retries from the harness).
- Runs report `tool_calls` and `iterations` per task so you can see whether
  the agent is gaining from its tools or spinning.

## Reproducing

```bash
# 1. Install with benchmark dependencies
pip install -e ".[docs]"

# 2. Fetch the public GAIA validation split (166 tasks with answers).
#    The dataset is GATED: accept the terms at
#    https://huggingface.co/datasets/gaia-benchmark/GAIA and set HF_TOKEN.
python -m james --eval gaia --download-gaia --eval-limit 10

# 3. Point JAMES at your LLM
#    (set OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY / GROQ_API_KEY
#    / OPENROUTER_API_KEY in .env or the environment)

# 4. Run a cheap subset first, then the full split
python -m james --eval gaia --eval-limit 10 --eval-iterations 10
python -m james --eval gaia
```

Notes:

- GAIA restructured to **Parquet** metadata (Oct 2025); the harness converts it
  to JSONL automatically (requires `pyarrow`, included in the `docs` extra).
- Reports land in `workspace/evaluations/gaia_report_*.json` with per-level
  pass rates, per-task tool usage, and worker errors.
- `--eval-iterations N` caps agent steps per task (default 20) — lower it to
  conserve free-tier token budgets.

## Offline smoke

`python -m james --eval smoke` runs the full evaluation pipeline (metrics,
report generation, level stratification) against a deterministic fake agent —
no network, no API key. It must always pass before a real run is trusted.

## Results

| Date | Provider / model | Split | Tasks | Pass rate | Level 1 | Level 2 | Level 3 | Command |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| (no runs yet) | — | validation | — | — | — | — | — | `--eval gaia` |

Runs are published here after they pass `--eval smoke`. The GitHub Actions
`Eval` workflow runs nightly (and on manual dispatch): it always runs the
offline smoke suite, executes a GAIA validation subset when an
`OPENAI_API_KEY` secret is configured, and then **auto-appends the run to the
table below** (`scripts/publish_benchmarks.py`) and commits the update. A
public repository without the secret still gets the offline smoke coverage.
