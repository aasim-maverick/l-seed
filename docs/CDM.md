# Context Dependency Mapper (CDM)

This document describes the core algorithm implemented in `cdm/mapper.py`.

## Goal

Given:

- a commit diff
- the files changed by that commit
- a repository import graph

CDM determines which extra files are required to reason correctly about the fix.

## Inputs and Outputs

## Input

| Input | Source |
|---|---|
| `changed_files: list[str]` | Commit file list from miner |
| `diff_text: str` | `git diff <sha>^ <sha>` |
| `language` | Repo config (`python`, `go`, `typescript`) |
| `subtree` | Optional path restriction, used mainly for TypeScript |

## Output

`ContextDependencyMap` object with:

- `required_context_files`
- `signal_details`
- `context_distance_hops`
- `irreducibility_score`
- `rfs`
- `cfrd`
- `constraint_bearing_files`
- `constraint_types`

Serialized shape via `to_dict()` includes `required_context_details`.

## Signal Model

CDM computes Required Context Score (RCS) per candidate file:

```text
RCS = 0.30 * ImportScore
    + 0.35 * TypeScore
    + 0.25 * StructuralScore
    + 0.10 * TestProximity
```

Files with `RCS < 0.18` are dropped.

### 1) ImportScore

- Based on shortest path from changed file to candidate in import graph
- Hop decay is geometric with base `0.6`

```text
hop 1 -> 1.00
hop 2 -> 0.60
hop 3 -> 0.36
```

### 2) TypeScore

- Extract symbols from diff and annotation positions
- Keep only symbols exported by candidate but not locally defined in changed files
- Annotation symbols get extra weight

This suppresses common symbol noise.

### 3) StructuralScore

Language specific structural coupling:

| Language | Signal |
|---|---|
| Go | Exported interface names used in diff |
| TypeScript | Exported interface or type alias usage |
| Python | ABC, Protocol, or TypedDict style usage |

### 4) TestProximity

Looks for test files close to changed files in graph neighborhood and boosts candidates in that closure.

## Task Level Metrics

### Irreducibility Score (IRR)

```text
irr = tanh(2.5 * mean(RCS))
```

Interpretation:

- near 0: mostly local change
- near 1: strong external context requirement

### Cross File Reasoning Depth (CFRD)

Implemented from pairwise coupling between all task files (changed plus required):

```text
CFRD(F) = average over pairs [ rho(fi, fj) * iota(fi, fj) ]
```

Where:

- `rho` is normalized graph path distance
- `iota` is interaction complexity based on shared exported symbols

### Reasoning Forcing Score (RFS)

```text
RFS = 0.30 * depth
    + 0.25 * iface
    + 0.20 * breadth
    + 0.15 * locality_deficit
    + 0.10 * cfrd
```

## Candidate Selection in `analyze()`

1. Build symbol sets from diff
2. Build candidate file set reachable within 3 hops of changed files
3. Compute four signals for each candidate
4. Keep candidates above relevance threshold
5. Sort by RCS descending
6. Compute IRR, CFRD, RFS
7. Return `ContextDependencyMap`

## Language Parser Adapters

| Parser | Key Notes |
|---|---|
| `cdm/languages/python_parser.py` | Handles `AnnAssign` exports and import edges |
| `cdm/languages/go_parser.py` | Uses module path package mapping and interface extraction |
| `cdm/languages/typescript_parser.py` | Excludes `.d.ts`, supports subtree restriction |

## Public API Example

```python
from cdm.mapper import ContextDependencyMapper

mapper = ContextDependencyMapper("data/repos/flask", language="python")
result = mapper.analyze(changed_files, diff_text, min_irr=0.20)

print(result.required_context_files)
print(result.irreducibility_score, result.rfs, result.cfrd)
print(result.to_dict())
```

## Testing

`cdm/tests/test_mapper.py` includes:

- diff symbol extraction checks
- CFRD behavior checks (star vs mesh)
- RFS bounds and monotonic trends
- output contract checks for `cfrd` and `rfs`

Run:

```bash
pytest cdm/tests -v
```
