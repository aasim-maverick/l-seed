# Repository Structure

Current high level structure:

```text
l-seed/
  analysis/
    aggregator.py
    failure-taxonomy.py
    metrics.py
    reporter.py
    visualizer.py
  cdm/
    mapper.py
    languages/
      go_parser.py
      python_parser.py
      typescript_parser.py
    tests/
      test_mapper.py
  codegen/
    eval_generator.py
    run_codegen.py
    templates/
      eval_template.ts.jinja2
  config/
    repo_manifest.json
  extraction/
    git_miner.py
    repo_scorer.py
    task_validator.py
    ts_miner.py
    viewer.py
  data/
    repos/
      flask/
      gin/
      typescript/
    tasks/
      raw/
      validated/
    results/
  docs/
    README.md
    QUICKSTART.md
    ARCHITECTURE.md
    CDM.md
    EXTRACTION_PIPELINE.md
    TASK_SCHEMA.md
    CODEGEN_AND_EVAL.md
    ANALYSIS_AND_REPORTING.md
    OPERATIONS.md
    REPO_STRUCTURE.md
  requirements.txt
  setup.py
```

## Data Directory Notes

- `data/repos/*` contains full repository snapshots and can be large.
- `data/tasks/raw` stores mined candidates.
- `data/tasks/validated` stores curated tasks used for eval generation.
- `data/results` stores run logs, analysis JSON, reports, and figures.
