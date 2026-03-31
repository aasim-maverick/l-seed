# Analysis and Reporting

This section documents run log metrics, aggregation outputs, and report generation.

Primary objective:

- deliver baseline Gemini CLI performance on this long context dataset
- identify failure modes that block deep multi file reasoning success

## Core Files

| File | Role |
|---|---|
| `analysis/metrics.py` | Computes run metrics and aggregate summaries |
| `analysis/aggregator.py` | Reads JSONL logs and writes analysis JSON |
| `analysis/reporter.py` | Creates Markdown reports |
| `analysis/visualizer.py` | Generates PNG figures |
| `analysis/failure-taxonomy.py` | Failure mode taxonomy and classifier logic |

## Data Inputs

1. Eval JSONL logs from generated tests
2. Validated task JSON records from `data/tasks/validated`

## Metric Set

Task level metrics:

- `CFRD`: cross file reasoning depth
- `RFS`: reasoning forcing score
- `IRR`: irreducibility score

Run level metrics:

- `ICU`: information coverage utilization
- `CCS`: context coverage score
- `PES`: path efficiency score
- `TER`: tool efficiency ratio

## Key Formulas

### ICU

Weighted retrieval quality with redundancy penalty:

```text
ICU = coverage * weighted_relevance / (redundancy + epsilon)
```

### PES

```text
PES = optimal_tool_calls / actual_tool_calls
```

With backtrack penalty in implementation.

### TER

```text
TER = productive_tool_calls / total_tool_calls
```

## Aggregation Output Contract

`analysis/aggregator.py` writes:

```json
{
  "model": "model-name",
  "run_date": "YYYY-MM-DD",
  "n_runs": 0,
  "pass_rate": 0.0,
  "avg_icu": 0.0,
  "avg_ccs": 0.0,
  "avg_pes": 0.0,
  "avg_ter": 0.0,
  "by_task": [],
  "by_difficulty": {},
  "failure_distribution": {},
  "task_metrics": {}
}
```

## Typical Commands

Aggregate one run:

```bash
python3 analysis/aggregator.py \
  --input data/results/<model>/<date>_run.jsonl \
  --tasks data/tasks/validated \
  --model <model-name> \
  --output data/results/<model>/<date>_analysis.json
```

Create single model report:

```bash
python3 analysis/reporter.py \
  --input data/results/<model>/<date>_analysis.json \
  --output data/results/<model>/<date>_report.md
```

Create model comparison JSON and report:

```bash
python3 analysis/aggregator.py \
  --input data/results/<model>/<date>_run.jsonl \
  --tasks data/tasks/validated \
  --model <model-name> \
  --output data/results/<model>/<date>_analysis.json \
  --compare data/results
```

```bash
python3 analysis/reporter.py \
  --compare data/results/comparison/model_comparison.json \
  --output data/results/comparison/report.md
```

Create figures:

```bash
python3 analysis/visualizer.py \
  --input data/results/*/*_analysis.json \
  --output data/results/figures
```

## Figure Types

`analysis/visualizer.py` currently emits:

- per task metric bars
- failure distribution pie
- ICU vs RFS scatter
- grouped model comparison bars

## Failure Taxonomy

The classifier maps runs to modes such as:

- `complete_hallucination`
- `context_insufficient`
- `wrong_files_targeted`
- `partial_fix`
- `shallow_fix`
- `retrieval_success_task_failure`
- `inefficient_progress`
- `success`

These modes improve diagnostic value beyond plain pass or fail.


• longcontext-bench/
  ├── analysis/                              # Post-run analytics, metrics, reporting, plots
  │   ├── aggregator.py                      # Aggregates JSONL eval logs into structured analysis JSON
  │   ├── failure-taxonomy.py                # Failure mode taxonomy and classifier utilities
  │   ├── metrics.py                         # Core metric computations (ICU, CCS, PES, TER, etc.)
  │   ├── reporter.py                        # Generates Markdown reports from analysis JSON
  │   └── visualizer.py                      # Generates figures (PNG) from analysis JSON
  │
  ├── cdm/                                   # Context Dependency Mapper core
  │   ├── __init__.py
  │   ├── mapper.py                          # Multi-signal dependency analysis + task complexity metrics
  │   ├── languages/                         # Language-specific import/symbol parsers
  │   │   ├── go_parser.py                   # Go parser for import graph + exported/interface symbols
  │   │   ├── python_parser.py               # Python parser for import graph + top-level symbols
  │   │   └── typescript_parser.py           # TypeScript parser with subtree + .d.ts filtering
  │   └── tests/
  │       └── test_mapper.py                 # Unit/integration tests for CDM logic
  │
  ├── codegen/                               # Task JSON -> Gemini eval TypeScript generation
  │   ├── eval_generator.py                  # Main generator with git snapshot loading + truncation rules
  │   ├── run_codegen.py                     # CLI wrapper to generate one/all eval files
  │   └── templates/
  │       └── eval_template.ts.jinja2        # Eval template with assertions + JSONL logging contract
  │
  ├── config/
  │   └── repo_manifest.json                 # Pinned repository manifest (id/url/sha/language)
  │
  ├── data/                                  # Dataset and run artifacts
  │   ├── repos/                             # Pinned source repos used for mining tasks
  │   │   ├── flask/
  │   │   ├── gin/
  │   │   └── typescript/
  │   ├── tasks/
  │   │   ├── raw/                           # Mined candidates before curation
  │   │   └── validated/                     # Curated tasks ready for eval generation/runs
  │   └── results/                           # Eval run logs, analyses, reports, figures
  │
  ├── docs/                                  # System design and operational docs
  │   ├── README.md
  │   ├── QUICKSTART.md
  │   ├── ARCHITECTURE.md
  │   ├── CDM.md
  │   ├── EXTRACTION_PIPELINE.md
  │   ├── TASK_SCHEMA.md
  │   ├── CODEGEN_AND_EVAL.md
  │   ├── ANALYSIS_AND_REPORTING.md
  │   ├── OPERATIONS.md
  │   └── REPO_STRUCTURE.md
  │
  ├── extraction/                            # Mining, review, and validation pipeline
  │   ├── git_miner.py                       # Main multi-repo miner using CDM
  │   ├── ts_miner.py                        # TypeScript-specific symbol-definition miner
  │   ├── repo_scorer.py                     # Repository candidacy scoring
  │   ├── task_validator.py                  # Validates task JSON schema + git coherence + semantics
  │   └── viewer.py                          # Interactive candidate viewer/tagger
  │
  ├── requirements.txt                       # Python dependencies
  ├── setup.py                               # Project scaffold helper (structure bootstrap)
  ├── quick_cdm_test.py                      # Local CDM smoke script
  ├── run_miner_day1_test.py                 # Legacy miner smoke script
  ├── read_task_files.py                     # Utility script for reading pinned task files
  ├── test_parser_quick.py                   # Parser/graph quick test
  ├── generate_flask_001_eval.py             # One-off helper script for flask-001 eval generation
  └── files_output.txt                       # Local artifact/utility output
