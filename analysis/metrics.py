"""
Metrics for LongContext-Bench evaluation runs.

Task-level metrics:
  CFRD, RFS, IRR

Run-level metrics:
  ICU, CCS, PES, TER

Task metrics are derived from task metadata and CDM output.
Run metrics are derived from evaluation JSONL logs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


ICU_EPSILON = 0.01


@dataclass
class TaskMetrics:
    """Task-level properties (fixed per task, from CDM output)."""
    task_id:  str
    cfrd:     float   
    rfs:      float
    irr:      float
    n_required: int
    n_changed:  int
@dataclass
class RunMetrics:
    """Agent-level measurements (from a single eval run log)."""
    task_id:          str
    icu:              float
    ccs:              float
    pes:              float
    ter:              float
    task_success:     bool
    failure_mode:     str
    total_calls:      int
    productive_calls: int
    backtrack_count:  int
    unique_reads:     int

@dataclass
class CombinedMetrics:
    """All six metrics for a single task run."""
    task:  TaskMetrics
    run:   RunMetrics

    @property
    def task_id(self) -> str:
        return self.task.task_id

    def as_dict(self) -> dict:
        return {
            "task_id":      self.task_id,
                        
            "cfrd":         self.task.cfrd,
            "rfs":          self.task.rfs,
            "irr":          self.task.irr,
            "n_required":   self.task.n_required,
            "n_changed":    self.task.n_changed,
                       
            "icu":          self.run.icu,
            "ccs":          self.run.ccs,
            "pes":          self.run.pes,
            "ter":          self.run.ter,
            "task_success": self.run.task_success,
            "failure_mode": self.run.failure_mode,
            "total_calls":  self.run.total_calls,
        }


                                                                                 

def extract_task_metrics(task_json: dict) -> TaskMetrics:
    """
    Extract task-level metrics from a validated task JSON record.
    The CDM fields (cfrd, rfs, irreducibility_score) are written by mapper.py.
    """
    cdm = task_json.get("cdm", task_json)                                 

    return TaskMetrics(
        task_id=task_json.get("task_id", cdm.get("task_id", "unknown")),
        cfrd=float(cdm.get("cfrd", 0.0)),
        rfs=float(cdm.get("rfs", 0.0)),
        irr=float(cdm.get("irreducibility_score", 0.0)),
        n_required=len(cdm.get("required_context_files", [])),
        n_changed=len(cdm.get("changed_files",
                              task_json.get("changed_files", []))),
    )


                                                                                 

def compute_icu(log_entry: dict, task_json: Optional[dict] = None) -> tuple[float, float]:
    """
    Formal ICU computation.

    ICU(W, I) = (|U(I)| / |I|) × (Σ_{u∈U(I)} τ(u)) / (ϕ(U(I)) + ε)

    Where:
      I     = required context files (the information set the agent needs)
      U(I)  = files in I that the agent actually successfully read
      τ(u)  = relevance weight of file u = its RCS score from CDM output
              (falls back to uniform 1.0 if RCS not available)
      ϕ(U(I)) = redundancy penalty = Σ_{u∈U(I)} max(0, reads(u) - 1)
      ε     = 0.01 (regularisation)

    Returns (icu, ccs) where ccs = |U(I)| / |I| is the plain coverage fraction.

    ICU > CCS when retrieved files have high relevance and low redundancy.
    ICU < CCS when retrieved files have low relevance or were read many times.
    ICU = CCS when all retrieved files have relevance = 1.0 and no redundancy.
    """
    required_files: list[str] = log_entry.get("requiredContextFiles", [])
    retrieval_results: list[dict] = log_entry.get("retrievalResults", [])

    if not required_files:
        return 1.0, 1.0                                            

                                                   
    tau: dict[str, float] = {}
    if task_json:
        cdm_details = task_json.get("cdm", {}).get("required_context_details", [])
        for d in cdm_details:
            tau[d["file"]] = float(d.get("rcs", 1.0))
                                
    for rf in required_files:
        tau.setdefault(rf, 1.0)

                                                           
    retrieved: list[tuple[str, int]] = []                      
    for r in retrieval_results:
        if r.get("retrieved", False):
            retrieved.append((r["file"], int(r.get("readCount", 1))))

    n_required = len(required_files)
    n_retrieved = len(retrieved)

                                                                      
    ccs = n_retrieved / n_required

    if n_retrieved == 0:
        return 0.0, 0.0

                                                          
    tau_sum = sum(tau.get(f, 1.0) for f, _ in retrieved)

                                                             
    phi = sum(max(0, count - 1) for _, count in retrieved)

                 
    icu = ccs * tau_sum / (phi + ICU_EPSILON)

                                                                                
    max_icu = 1.0 / ICU_EPSILON                                           
                                                                              
                                            
    ideal_icu = 1.0 * n_required / ICU_EPSILON
    icu_normalised = min(1.0, icu / max(ICU_EPSILON, ideal_icu) * n_required)

    return round(min(1.0, icu_normalised), 4), round(ccs, 4)


                                                                                

def compute_pes(log_entry: dict) -> tuple[float, int]:
    """
    PES = optimal_tool_calls / actual_tool_calls

    Optimal path estimate:
      - One read per required context file
      - One read per changed file (agent surveys before editing)
      - One write per changed file

    Backtrack count = files written more than once (indicates wrong-first-edit).
    PES is penalised for backtracks: final PES = base_PES × (1 - 0.1 × backtracks)
    capped at [0, 1].
    """
    required_files = log_entry.get("requiredContextFiles", [])
    changed_files  = log_entry.get("changedFiles", [])
    total_calls    = log_entry.get("toolCallCount", 1)

    optimal_reads  = len(required_files) + len(changed_files)
    optimal_writes = len(changed_files)
    optimal_total  = optimal_reads + optimal_writes

    if total_calls == 0:
        return 0.0, 0

    sequence = log_entry.get("operationSequenceSummary", [])
    write_counts: dict[str, int] = {}
    for op in sequence:
        if op.get("op") == "write":
            write_counts[op.get("file", "")] = write_counts.get(op.get("file", ""), 0) + 1
    backtrack_count = sum(max(0, c - 1) for c in write_counts.values())

    base_pes = optimal_total / max(1, total_calls)
    backtrack_penalty = min(0.5, backtrack_count * 0.10)                      
    pes = max(0.0, base_pes * (1 - backtrack_penalty))
    return round(min(1.0, pes), 4), backtrack_count


                                                                                

def compute_ter(log_entry: dict) -> tuple[float, int]:
    """
    TER = productive_tool_calls / total_tool_calls

    Productive calls:
      - Read of a required context file (confirmed by retrievalResults)
      - Write to a changed file that differs from baseline (from modifiedFiles)
      - Unique exploratory reads that plausibly preceded productive edits

    Non-productive calls:
      - Repeated reads of already-read files
      - Failed tool calls
      - Reads with no subsequent edit to a related file
    """
    total_calls        = log_entry.get("toolCallCount", 0)
    retrieval_results  = log_entry.get("retrievalResults", [])
    modified_files     = log_entry.get("agentModifiedFiles", [])
    unique_reads       = log_entry.get("uniqueReadPaths", 0)
    required_files     = log_entry.get("requiredContextFiles", [])

    if total_calls == 0:
        return 0.0, 0

    req_reads_done = sum(1 for r in retrieval_results if r.get("retrieved", False))
    productive_writes = len(modified_files)
                                                                                     
    exploration_credit = min(
        max(0, unique_reads - len(required_files)),
        productive_writes
    )
    productive = req_reads_done + productive_writes + exploration_credit
    ter = productive / total_calls
    return round(min(1.0, ter), 4), productive


                                                                                

def compute_run_metrics(log_entry: dict, task_json: Optional[dict] = None) -> RunMetrics:
    icu, ccs = compute_icu(log_entry, task_json)
    pes, backtrack = compute_pes(log_entry)
    ter, productive = compute_ter(log_entry)

    return RunMetrics(
        task_id=log_entry.get("taskId", "unknown"),
        icu=icu, ccs=ccs, pes=pes, ter=ter,
        task_success=log_entry.get("taskSuccess", False),
        failure_mode=log_entry.get("failureMode", "unknown"),
        total_calls=log_entry.get("toolCallCount", 0),
        productive_calls=productive,
        backtrack_count=backtrack,
        unique_reads=log_entry.get("uniqueReadPaths", 0),
    )


def aggregate_runs(log_entries: list[dict], task_jsons: Optional[dict[str, dict]] = None) -> dict:
    """
    Aggregate all six metrics across multiple eval runs.

    task_jsons: mapping of task_id → task JSON record (for τ weights in ICU).
    """
    if not log_entries:
        return {}

    all_runs: list[RunMetrics] = []
    for entry in log_entries:
        tid = entry.get("taskId", "")
        tj = (task_jsons or {}).get(tid)
        all_runs.append(compute_run_metrics(entry, tj))

    n = len(all_runs)
    passed = [r for r in all_runs if r.task_success]

    def avg(vals: list[float]) -> float:
        return round(sum(vals) / len(vals), 4) if vals else 0.0

                         
    by_difficulty: dict[str, list[RunMetrics]] = {}
    for entry, run in zip(log_entries, all_runs):
        level = entry.get("difficultyLevel", "unknown")
        by_difficulty.setdefault(level, []).append(run)

    by_diff_summary = {
        level: {
            "n": len(runs),
            "pass_rate": round(sum(1 for r in runs if r.task_success) / len(runs), 3),
            "avg_icu": avg([r.icu for r in runs]),
            "avg_ccs": avg([r.ccs for r in runs]),
            "avg_pes": avg([r.pes for r in runs]),
            "avg_ter": avg([r.ter for r in runs]),
        }
        for level, runs in by_difficulty.items()
    }

                               
    failure_dist: dict[str, int] = {}
    for r in all_runs:
        failure_dist[r.failure_mode] = failure_dist.get(r.failure_mode, 0) + 1

                                                            
    icu_on_pass = avg([r.icu for r in passed])
    icu_on_fail = avg([r.icu for r in all_runs if not r.task_success])

    return {
        "n_runs": n,
        "pass_rate": round(len(passed) / n, 3),
                               
        "avg_icu": avg([r.icu for r in all_runs]),
        "avg_ccs": avg([r.ccs for r in all_runs]),
        "avg_pes": avg([r.pes for r in all_runs]),
        "avg_ter": avg([r.ter for r in all_runs]),
                           
        "avg_icu_on_pass": icu_on_pass,
        "avg_icu_on_fail": icu_on_fail,
        "icu_gap": round(icu_on_pass - icu_on_fail, 4),
                   
        "by_difficulty": by_diff_summary,
        "failure_distribution": failure_dist,
    }


def load_log_entries(jsonl_path: str | Path) -> list[dict]:
    """Read all entries from a JSONL benchmark log file."""
    entries = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries
