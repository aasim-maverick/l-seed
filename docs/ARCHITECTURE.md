# Architecture

## System Layers

L-SEED is organized into five layers for a large scale dataset program:

1. Repository curation and pinned snapshots
2. CDM reasoning and dependency analysis
3. Task extraction and curation pipeline
4. Gemini CLI eval generation and integration
5. Baseline analytics and failure mode reporting

## Component Graph

```mermaid
flowchart TB
    subgraph Data["Data Layer"]
      R[data/repos]
      TR[data/tasks/raw]
      TV[data/tasks/validated]
      RES[data/results]
    end

    subgraph CDM["CDM Layer"]
      M[cdm/mapper.py]
      PY[python_parser.py]
      GO[go_parser.py]
      TS[typescript_parser.py]
    end

    subgraph Extraction["Extraction Layer"]
      GM[extraction/git_miner.py]
      TSM[extraction/ts_miner.py]
      VIEW[extraction/viewer.py]
      VAL[extraction/task_validator.py]
      SCORE[extraction/repo_scorer.py]
    end

    subgraph Codegen["Codegen Layer"]
      EG[codegen/eval_generator.py]
      TMP[eval_template.ts.jinja2]
    end

    subgraph Analysis["Analysis Layer"]
      MET[analysis/metrics.py]
      AGG[analysis/aggregator.py]
      REP[analysis/reporter.py]
      VIZ[analysis/visualizer.py]
    end

    R --> GM
    R --> TSM
    GM --> M
    M --> PY
    M --> GO
    M --> TS
    GM --> TR
    TSM --> TR
    TR --> VIEW
    VIEW --> TV
    TV --> VAL
    TV --> EG
    TMP --> EG
    EG --> RES
    RES --> AGG
    AGG --> REP
    AGG --> VIZ
    MET --> AGG
```

## Package Responsibilities

| Directory | Responsibility |
|---|---|
| `cdm/` | Build import graphs, compute required context, and derive complexity metrics |
| `extraction/` | Discover commit candidates and curate them into task artifacts |
| `codegen/` | Convert validated task JSON into executable eval `.ts` files |
| `analysis/` | Compute benchmark metrics from JSONL logs and generate outputs |
| `data/` | Store repos, raw candidates, validated tasks, and results |
| `config/` | Track pinned repo metadata and language mapping |

## Runtime Data Contracts

### Contract A: Candidate Record

- Produced by `extraction/git_miner.py` and `extraction/ts_miner.py`
- Stored under `data/tasks/raw/*_candidates.json`
- Must include top level commit metadata plus nested `cdm` block

### Contract B: Validated Task Record

- Stored under `data/tasks/validated/*.json`
- Includes prompt, constraint assertions, and runtime metadata used by codegen

### Contract C: Eval Run Log Entry

- JSONL entries produced by generated eval tests
- Consumed by `analysis/aggregator.py` and `analysis/metrics.py`

### Contract D: Analysis JSON

- Structured summary produced by aggregator
- Consumed by reporter and visualizer

## Execution Sequence

```mermaid
sequenceDiagram
    participant Curate as Repo Curation
    participant Miner as extraction/git_miner.py
    participant CDM as cdm/mapper.py
    participant Raw as data/tasks/raw
    participant Validate as viewer + task_validator
    participant Valid as data/tasks/validated
    participant Gen as codegen/eval_generator.py
    participant Eval as Gemini CLI eval .ts
    participant Log as JSONL logs
    participant Agg as analysis/aggregator.py

    Curate->>Miner: select and pin repositories
    Miner->>CDM: analyze(changed_files, diff)
    CDM-->>Miner: ContextDependencyMap
    Miner->>Raw: write candidates JSON
    Validate->>Raw: review and tag
    Validate->>Valid: finalize validated task JSON
    Gen->>Valid: load task
    Gen->>Eval: write eval .ts
    Eval->>Log: append run entries
    Agg->>Log: parse entries
    Agg-->>Validate: baseline analysis JSON
```

## Design Notes

- Architecture is designed to scale from a small seed corpus to 30-50 repositories.
- CDM is language aware through parser adapters, while scoring logic stays centralized in `cdm/mapper.py`.
- TypeScript has a second miner (`extraction/ts_miner.py`) because compiler namespace exports can flatten import graph quality.
- Analysis keeps task level properties separate from run level behavior to avoid metric leakage.
