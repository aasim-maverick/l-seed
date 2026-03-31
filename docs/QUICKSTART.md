# Quickstart

This guide gets you from a fresh checkout to a complete local run of mining, validation, and analysis.

The long term project target is a 30-50 repository long context dataset integrated with Gemini CLI eval workflows. The current repository provides the core pipeline and seed data to build toward that target.

## 1) Prerequisites

| Requirement | Notes |
|---|---|
| Python | Use `python3` |
| Git | Required for commit and file snapshot reads |
| Node.js + npm | Needed only for running generated eval `.ts` files in Gemini CLI |

## 2) Install Python Dependencies

From repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Optional but useful for better TypeScript parsing support:

```bash
pip install tree-sitter-typescript
```

## 3) Verify Basic Environment

```bash
python3 -m py_compile cdm/mapper.py extraction/git_miner.py codegen/eval_generator.py
```

## 4) Mine Candidates

General miner:

```bash
python3 extraction/git_miner.py
```

TypeScript specific miner:

```bash
python3 extraction/ts_miner.py
```

Outputs:

- `data/tasks/raw/flask_candidates.json`
- `data/tasks/raw/gin_candidates.json`
- `data/tasks/raw/typescript_candidates.json`
- `data/tasks/raw/all_candidates.json`

## 5) Review and Tag

Interactive review:

```bash
python3 extraction/viewer.py --repo flask
```

Show summary of tags:

```bash
python3 extraction/viewer.py --tagged
```

## 6) Validate Task JSON Files

```bash
python3 extraction/task_validator.py data/tasks/validated/*.json --strict
```

## 7) Generate Eval TypeScript Files

```bash
python3 codegen/run_codegen.py --tasks flask-001 --gemini-cli ../gemini-cli
```

Dry run:

```bash
python3 codegen/run_codegen.py --dry-run
```

## 8) Aggregate and Report

```bash
python3 analysis/aggregator.py \
  --input data/results/<model>/<date>_run.jsonl \
  --tasks data/tasks/validated \
  --model <model-name> \
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

## 9) Common First Run Pitfalls

| Symptom | Likely Cause | Fix |
|---|---|---|
| `repo not found at data/repos/<id>` | Missing cloned pinned repo | Add repo under `data/repos/<id>` |
| `python: command not found` | Shell does not expose `python` alias | Use `python3` |
| No candidates found | Filters too strict or recent commits not qualifying | Increase `n_commits` or lower `min_irr` in miner config |
| Generated eval files are too large | Large source files in context | Keep current truncation defaults in codegen |
