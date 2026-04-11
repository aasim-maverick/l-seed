# Operations Playbook

This playbook is a practical runbook for maintainers.

## Program Milestones

1. Curate 30-50 large active repositories.
2. Extract a stable pool of long context tasks per repository.
3. Validate schema quality and reproducibility.
4. Integrate generated evals into Gemini CLI test runs.
5. Publish baseline metrics and failure mode reports.

## Daily Workflow

1. Confirm repos are present and pinned under `data/repos/*`.
2. Run miner(s) and inspect candidate counts.
3. Review candidates and curate validated tasks.
4. Generate eval files for selected tasks.
5. Run model evals and capture JSONL logs.
6. Aggregate logs and generate reports and figures.

## End To End Checklist

## A) Mine

```bash
python3 extraction/git_miner.py
python3 extraction/ts_miner.py
```

Checkpoint:

- candidate JSON files exist in `data/tasks/raw`
- counts are non zero for target repos

## B) Curate

```bash
python3 extraction/viewer.py --repo flask
python3 extraction/viewer.py --tagged
```

Checkpoint:

- `tagged_candidates.json` updated
- candidate selections documented in task notes

## C) Validate

```bash
python3 extraction/task_validator.py data/tasks/validated/*.json --strict
```

Checkpoint:

- strict validation exits clean

## D) Generate Evals

```bash
python3 codegen/run_codegen.py --tasks flask-001 --gemini-cli ../gemini-cli
```

Checkpoint:

- eval file created in `../gemini-cli/evals/l-seed/`

## E) Run Evals

```bash
cd ../gemini-cli
RUN_EVALS=1 GEMINI_MODEL=<model> \
L_SEED_LOG=../<l-seed-repo>/data/results/<model>/<date>_run.jsonl \
npx vitest run evals/l-seed/*.eval.ts
```

Checkpoint:

- JSONL log file exists and has entries

## F) Analyze

```bash
cd ../<l-seed-repo>
python3 analysis/aggregator.py \
  --input data/results/<model>/<date>_run.jsonl \
  --tasks data/tasks/validated \
  --model <model> \
  --output data/results/<model>/<date>_analysis.json
```

```bash
python3 analysis/reporter.py \
  --input data/results/<model>/<date>_analysis.json \
  --output data/results/<model>/<date>_report.md
```

```bash
python3 analysis/visualizer.py \
  --input data/results/<model>/<date>_analysis.json \
  --output data/results/figures
```

## Troubleshooting

| Problem | Cause | Action |
|---|---|---|
| Miner skips all commits | strict filters | increase `n_commits` and lower `min_irr` temporarily |
| Required file missing during validation | stale SHA or path drift | verify `commit_sha` and file path with `git cat-file -e` |
| Eval file too large | large required files | keep symbol guided extraction defaults |
| Low ICU but high CCS | redundant reads or low relevance files | inspect retrieval sequence and target high RCS files |
| Report has zero tasks | wrong JSONL input path | confirm run log path and model directory |

## Data Hygiene Rules

- Do not overwrite validated tasks without review notes.
- Keep commit SHAs pinned and reproducible.
- Record parameter changes in miner configs when rerunning.
- Keep generated reports versioned by date and model.

## Suggested Git Practice

- One commit for mining logic changes
- One commit for task JSON changes
- One commit for docs and reports

This keeps diffs reviewable and rollback friendly.
