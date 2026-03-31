"""
Reads JSONL eval logs and produces structured analysis JSON files.

OUTPUT FORMAT (data/results/<model>/<date>_analysis.json):
{
  "model": "gemini-2.5-flash",
  "run_date": "2026-03-31",
  "n_tasks": 8,
  "pass_rate": 0.375,
  "metrics": { "avg_icu": 0.61, "avg_pes": 0.52, ... },
  "by_task": [ { "task_id": "flask-001", "icu": 0.0, "pes": 0.0, ... }, ... ],
  "by_difficulty": { "easy": {...}, "medium": {...}, "hard": {...} },
  "failure_distribution": { "context_substitution": 3, "success": 3, ... },
  "task_metrics": { "flask-001": { "cfrd": 0.31, "rfs": 0.58, ... }, ... }
}

DATA DIRECTORY CONVENTION:
  data/results/
  ├── gemini-2.5-flash/
  │   ├── 2026-03-31_run.jsonl          Raw JSONL log from eval run
  │   └── 2026-03-31_analysis.json      Structured analysis (this script)
  ├── gemini-2.5-pro/
  │   └── ...
  └── comparison/
      └── model_comparison.json         Cross-model comparison table

USAGE:
  python analysis/aggregator.py \\
      --input data/results/gemini-2.5-flash/2026-03-31_run.jsonl \\
      --tasks data/tasks/validated \\
      --model gemini-2.5-flash \\
      --output data/results/gemini-2.5-flash/2026-03-31_analysis.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from analysis.metrics import (
    aggregate_runs,
    compute_run_metrics,
    extract_task_metrics,
    load_log_entries,
)


def load_task_jsons(task_dir: Path) -> dict[str, dict]:
    """Load all validated task JSON records, keyed by task_id."""
    tasks: dict[str, dict] = {}
    for p in task_dir.glob("*.json"):
        try:
            with open(p, encoding="utf-8") as f:
                t = json.load(f)
            tasks[t["task_id"]] = t
        except Exception as e:
            print(f"  [WARN] Could not load {p.name}: {e}")
    return tasks


def aggregate(
    jsonl_path: Path,
    task_dir: Path,
    model: str,
    output_path: Path,
) -> dict:
    """
    Read a JSONL run log and produce a structured analysis dict.
    Saves to output_path and returns the dict.
    """
    print(f"\nAggregating {jsonl_path.name} …")
    entries = load_log_entries(jsonl_path)
    if not entries:
        print("  No log entries found.")
        return {}

    task_jsons = load_task_jsons(task_dir)
    print(f"  {len(entries)} log entries, {len(task_jsons)} task records")

                                 
    task_metrics: dict[str, dict] = {}
    for tid, tj in task_jsons.items():
        try:
            tm = extract_task_metrics(tj)
            task_metrics[tid] = {
                "cfrd": tm.cfrd,
                "rfs": tm.rfs,
                "irr": tm.irr,
                "n_required": tm.n_required,
                "n_changed": tm.n_changed,
            }
        except Exception as e:
            print(f"  [WARN] Could not extract task metrics for {tid}: {e}")

                     
    per_task: list[dict] = []
    for entry in entries:
        tid = entry.get("taskId", "unknown")
        tj = task_jsons.get(tid)
        run = compute_run_metrics(entry, tj)
        per_task.append({
            "task_id": tid,
            "difficulty_level": entry.get("difficultyLevel", "unknown"),
            "language": entry.get("language", "unknown"),
            "task_success": run.task_success,
            "failure_mode": run.failure_mode,
            "icu": run.icu,
            "ccs": run.ccs,
            "pes": run.pes,
            "ter": run.ter,
            "cfrd": task_metrics.get(tid, {}).get("cfrd", 0.0),
            "rfs": task_metrics.get(tid, {}).get("rfs", 0.0),
            "irr": task_metrics.get(tid, {}).get("irr", 0.0),
            "total_calls": run.total_calls,
            "backtrack_count": run.backtrack_count,
        })

                       
    agg = aggregate_runs(entries, task_jsons)

    result = {
        "model": model,
        "run_date": datetime.utcnow().strftime("%Y-%m-%d"),
        "source_log": str(jsonl_path),
        **agg,
        "by_task": per_task,
        "task_metrics": task_metrics,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"  Written → {output_path}")
    print(f"  Pass rate: {agg.get('pass_rate', 0):.1%}  |  "
          f"avg ICU: {agg.get('avg_icu', 0):.3f}  |  "
          f"avg PES: {agg.get('avg_pes', 0):.3f}")
    return result


def compare_models(analysis_dir: Path, output_path: Path) -> None:
    """
    Read per-model analysis JSONs and produce a comparison table.
    """
    analyses = []
    for p in sorted(analysis_dir.rglob("*_analysis.json")):
        with open(p, encoding="utf-8") as f:
            analyses.append(json.load(f))

    if not analyses:
        print("No analysis files found for comparison.")
        return

    comparison = {
        "generated": datetime.utcnow().isoformat(),
        "models": [
            {
                "model": a.get("model"),
                "run_date": a.get("run_date"),
                "n_tasks": a.get("n_runs", 0),
                "pass_rate": a.get("pass_rate", 0),
                "avg_icu": a.get("avg_icu", 0),
                "avg_ccs": a.get("avg_ccs", 0),
                "avg_pes": a.get("avg_pes", 0),
                "avg_ter": a.get("avg_ter", 0),
                "icu_gap": a.get("icu_gap", 0),
            }
            for a in analyses
        ]
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(comparison, f, indent=2)
    print(f"\nModel comparison → {output_path}")

                          
    print("\n| Model | Pass% | ICU | CCS | PES | TER |")
    print("|-------|-------|-----|-----|-----|-----|")
    for m in comparison["models"]:
        print(f"| {m['model']} | {m['pass_rate']:.0%} | "
              f"{m['avg_icu']:.3f} | {m['avg_ccs']:.3f} | "
              f"{m['avg_pes']:.3f} | {m['avg_ter']:.3f} |")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True,
                        help="JSONL log file from eval run")
    parser.add_argument("--tasks", default="data/tasks/validated",
                        help="Directory of validated task JSON records")
    parser.add_argument("--model", default="unknown",
                        help="Model name (e.g. gemini-2.5-flash)")
    parser.add_argument("--output", required=True,
                        help="Output analysis JSON path")
    parser.add_argument("--compare", default=None,
                        help="If set, also produce cross-model comparison in this dir")
    args = parser.parse_args()

    aggregate(
        jsonl_path=Path(args.input),
        task_dir=Path(args.tasks),
        model=args.model,
        output_path=Path(args.output),
    )

    if args.compare:
        compare_models(
            analysis_dir=Path(args.compare),
            output_path=Path(args.compare) / "comparison" / "model_comparison.json",
        )


if __name__ == "__main__":
    main()