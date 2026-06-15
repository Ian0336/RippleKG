# Additional RippleKG Evaluation Scripts

This README documents the additional experiment scripts added for the final report.
They are designed to work with the current `main` branch of `Ian0336/RippleKG` and
reuse the existing benchmark implementation instead of changing the core M1/M2
pipeline.

## Added scripts

Place these files under `scripts/`:

| Script | Purpose | Main outputs |
|---|---|---|
| `run_scale_experiments.py` | Runs `scripts/run_benchmark.py` over multiple corpus sizes and collects scaling results. | `data/scale_experiments.csv`, `data/scale_experiments.json` |
| `summarize_benchmark_rows.py` | Aggregates `run_benchmark.py --rows-jsonl` output by scenario. | `data/scenario_summary.csv`, `data/scenario_summary.json` |
| `run_timed_benchmark.py` | Measures wall-clock runtime and counts durable maintenance-log records after a benchmark run. | `data/timed_benchmark.json`, `data/timed_benchmark_stdout.txt` |
| `collect_final_tables.py` | Combines the scale, scenario, and timing results into a copyable Markdown summary. | `data/final_tables.md` |

These scripts are collectors/wrappers. They do not implement a new baseline and do
not alter the RippleKG maintenance logic. The current main benchmark already
reports the important baseline quantities, including affected-evidence rebuild,
document-level rebuild, whole-KG rebuild, B0 consistency, B1 generic traversal,
and B2 naive invalidation.

## Prerequisites

From the repository root, make sure the Docker services and T0 graph are ready:

```bat
docker compose up -d --build
docker compose exec api python scripts/init_db.py
docker compose exec api python scripts/ingest_t0.py data/docred/dev_revised.json 50
```

If `data/docred/dev_revised.json` is missing, download it first:

```bat
mkdir data\docred
curl.exe -L -o data\docred\dev_revised.json https://raw.githubusercontent.com/tonytan48/Re-DocRED/main/data/dev_revised.json
```

## 1. Generate the main benchmark rows

This command creates the row-level input used by `summarize_benchmark_rows.py`:

```bat
docker compose exec api python scripts/run_benchmark.py --docs 50 --edits 100 --mode mixed --json --rows-jsonl data/baseline_rows.jsonl --rows-csv data/baseline_rows.csv > data/baseline_summary.json
```

Outputs:

```text
data/baseline_summary.json
data/baseline_rows.jsonl
data/baseline_rows.csv
```

## 2. Scenario breakdown

```bat
docker compose exec api python scripts/summarize_benchmark_rows.py --input data/baseline_rows.jsonl --output data/scenario_summary.csv --json-output data/scenario_summary.json
```

Use this table to show which edit types benefit most from evidence-aware
maintenance. In the final report, it supports the claim that semantic no-op and
non-evidence edits are mostly skipped, while true relation removals trigger
necessary patch/rebuild work.

## 3. Scaling experiment

```bat
docker compose exec api python scripts/run_scale_experiments.py --docs-list 5,10,25,50,100,200,500 --edits 100 --mode mixed --output data/scale_experiments.csv --json-output data/scale_experiments.json
```

Use this table to show how RippleKG scales with changed evidence rather than
corpus size. Smaller corpora may contain fewer eligible synthetic edits; this is
expected.

## 4. Runtime and maintenance-log overhead

```bat
docker compose exec api python scripts/run_timed_benchmark.py --docs 50 --edits 100 --mode mixed --output data/timed_benchmark.json
```

This records:

- total wall-clock runtime,
- milliseconds per edit,
- number of `evidence_deltas` records,
- number of `refresh_decisions` records,
- log records per edit,
- B0 consistency results.

Use this to supplement the nominal cost model with an operational measurement.

## 5. Collect final report tables

```bat
docker compose exec api python scripts/collect_final_tables.py --output data/final_tables.md
```

The generated Markdown file combines the scale table, scenario table, and timing
summary for easy copy/paste into the report.

## Recommended command sequence

```bat
REM Main benchmark + row logs
docker compose exec api python scripts/run_benchmark.py --docs 50 --edits 100 --mode mixed --json --rows-jsonl data/baseline_rows.jsonl --rows-csv data/baseline_rows.csv > data/baseline_summary.json

REM Scaling
docker compose exec api python scripts/run_scale_experiments.py --docs-list 5,10,25,50,100,200,500 --edits 100 --mode mixed --output data/scale_experiments.csv --json-output data/scale_experiments.json

REM Scenario breakdown
docker compose exec api python scripts/summarize_benchmark_rows.py --input data/baseline_rows.jsonl --output data/scenario_summary.csv --json-output data/scenario_summary.json

REM Runtime/log overhead
docker compose exec api python scripts/run_timed_benchmark.py --docs 50 --edits 100 --mode mixed --output data/timed_benchmark.json

REM Final Markdown tables
docker compose exec api python scripts/collect_final_tables.py --output data/final_tables.md
```

## Result files and Git policy

The generated files under `data/` are experiment outputs and should not be
committed. Commit only the scripts and this README.

Recommended local-only ignore rule:

```bat
echo data/>> .git\info\exclude
```

Before committing, check staged files carefully:

```bat
git status --short
git diff --cached --name-only
```

The staged list should contain only files such as:

```text
ADDITIONAL_EXPERIMENTS_README.md
scripts/run_scale_experiments.py
scripts/summarize_benchmark_rows.py
scripts/run_timed_benchmark.py
scripts/collect_final_tables.py
```

It should not contain `data/*.csv`, `data/*.json`, `data/*.jsonl`, or
`data/*.md` result files.

## Suggested branch and push commands

```bat
git checkout main
git pull origin main
git checkout -b exp/additional-evaluation-scripts

echo data/>> .git\info\exclude

git add ADDITIONAL_EXPERIMENTS_README.md
git add scripts/run_scale_experiments.py scripts/summarize_benchmark_rows.py scripts/run_timed_benchmark.py scripts/collect_final_tables.py

git status --short
git diff --cached --name-only

git commit -m "Add additional evaluation experiment scripts"
git push -u origin exp/additional-evaluation-scripts
```

If you already created the branch, use:

```bat
git checkout exp/additional-evaluation-scripts
git pull origin main
```

Then repeat the `git add`, `git commit`, and `git push` steps.
