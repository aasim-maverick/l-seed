"""
analysis/reporter.py

Generates human-readable markdown benchmark reports from analysis JSON files.

USAGE:
  python analysis/reporter.py \\
      --input data/results/gemini-2.5-flash/2026-03-31_analysis.json \\
      --output data/results/gemini-2.5-flash/2026-03-31_report.md

  # Cross-model comparison report
  python analysis/reporter.py \\
      --compare data/results/comparison/model_comparison.json \\
      --output data/results/comparison/report.md
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


def _pct(v: float) -> str:
    return f"{v:.1%}"

def _f3(v: float) -> str:
    return f"{v:.3f}"

def _bar(v: float, width: int = 20) -> str:
    filled = int(round(v * width))
    return "█" * filled + "░" * (width - filled)


def generate_report(analysis: dict, output_path: Path) -> str:
    model      = analysis.get("model", "unknown")
    run_date   = analysis.get("run_date", "unknown")
    n          = analysis.get("n_runs", 0)
    pass_rate  = analysis.get("pass_rate", 0)
    by_task    = analysis.get("by_task", [])
    by_diff    = analysis.get("by_difficulty", {})
    fail_dist  = analysis.get("failure_distribution", {})
    task_mets  = analysis.get("task_metrics", {})

    lines = [
        f"# L-SEED: {model} Evaluation Report",
        f"\n**Run date:** {run_date}  |  **Tasks:** {n}  |  "
        f"**Pass rate:** {_pct(pass_rate)}",
        "",
        "---",
        "",
        "## Overall Metrics",
        "",
        "| Metric | Score | Description |",
        "|--------|-------|-------------|",
        f"| **ICU** | {_f3(analysis.get('avg_icu', 0))} | "
        "Information Coverage Utilization (weighted retrieval) |",
        f"| **CCS** | {_f3(analysis.get('avg_ccs', 0))} | "
        "Context Coverage Score (fraction of required files read) |",
        f"| **PES** | {_f3(analysis.get('avg_pes', 0))} | "
        "Path Efficiency Score (optimal / actual tool calls) |",
        f"| **TER** | {_f3(analysis.get('avg_ter', 0))} | "
        "Tool Efficiency Ratio (productive / total calls) |",
        "",
        f"**ICU gap** (pass − fail): "
        f"`{_f3(analysis.get('avg_icu_on_pass', 0))}` vs "
        f"`{_f3(analysis.get('avg_icu_on_fail', 0))}` = "
        f"**{_f3(analysis.get('icu_gap', 0))}**",
        "",
        "_ICU gap measures how much better the agent's context retrieval was "
        "on tasks it passed vs failed. A large gap indicates retrieval quality "
        "is a primary driver of task success._",
        "",
        "---",
        "",
        "## Results by Task",
        "",
        "| Task | Difficulty | Language | Pass | ICU | CCS | PES | TER | "
        "CFRD | RFS | Failure Mode |",
        "|------|-----------|---------|------|-----|-----|-----|-----|------|-----|-------------|",
    ]

    for t in sorted(by_task, key=lambda x: x.get("task_id", "")):
        mark   = "✅" if t.get("task_success") else "❌"
        mode   = t.get("failure_mode", "").replace("_", "\\_")
        lines.append(
            f"| {t['task_id']} | {t.get('difficulty_level', '?')} | "
            f"{t.get('language', '?')} | {mark} | "
            f"{_f3(t.get('icu', 0))} | {_f3(t.get('ccs', 0))} | "
            f"{_f3(t.get('pes', 0))} | {_f3(t.get('ter', 0))} | "
            f"{_f3(t.get('cfrd', 0))} | {_f3(t.get('rfs', 0))} | "
            f"`{mode}` |"
        )

    lines += [
        "",
        "---",
        "",
        "## Results by Difficulty",
        "",
        "| Level | N | Pass% | ICU | CCS | PES | TER |",
        "|-------|---|-------|-----|-----|-----|-----|",
    ]
    for level in ("easy", "medium", "hard", "unknown"):
        if level not in by_diff:
            continue
        d = by_diff[level]
        lines.append(
            f"| {level.capitalize()} | {d['n']} | "
            f"{_pct(d['pass_rate'])} | "
            f"{_f3(d.get('avg_icu', 0))} | {_f3(d.get('avg_ccs', 0))} | "
            f"{_f3(d.get('avg_pes', 0))} | {_f3(d.get('avg_ter', 0))} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Failure Mode Distribution",
        "",
        "| Mode | Count | % | Bar |",
        "|------|-------|---|-----|",
    ]
    total_fails = sum(fail_dist.values())
    for mode, count in sorted(fail_dist.items(), key=lambda x: -x[1]):
        pct = count / max(1, total_fails)
        lines.append(
            f"| `{mode}` | {count} | {_pct(pct)} | {_bar(pct, 15)} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Task-Level Complexity (CDM Metrics)",
        "",
        "_These measure task difficulty, not agent performance._",
        "",
        "| Task | CFRD | RFS | IRR | Required Files |",
        "|------|------|-----|-----|----------------|",
    ]
    for tid, tm in sorted(task_mets.items()):
        lines.append(
            f"| {tid} | {_f3(tm.get('cfrd', 0))} | "
            f"{_f3(tm.get('rfs', 0))} | "
            f"{_f3(tm.get('irr', 0))} | "
            f"{tm.get('n_required', 0)} |"
        )

    lines += [
        "",
        "---",
        "",
        f"_Generated by L-SEED reporter on "
        f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}_",
    ]

    content = "\n".join(lines) + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    print(f"Report written → {output_path}")
    return content


def generate_comparison_report(comparison: dict, output_path: Path) -> str:
    models = comparison.get("models", [])
    lines = [
        "# L-SEED: Model Comparison",
        f"\n_Generated: {comparison.get('generated', 'unknown')}_",
        "",
        "| Model | Pass% | ICU | CCS | PES | TER | ICU Gap |",
        "|-------|-------|-----|-----|-----|-----|---------|",
    ]
    for m in models:
        lines.append(
            f"| **{m['model']}** | {_pct(m['pass_rate'])} | "
            f"{_f3(m['avg_icu'])} | {_f3(m['avg_ccs'])} | "
            f"{_f3(m['avg_pes'])} | {_f3(m['avg_ter'])} | "
            f"{_f3(m.get('icu_gap', 0))} |"
        )

    content = "\n".join(lines) + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    print(f"Comparison report → {output_path}")
    return content


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=None, help="Analysis JSON path")
    parser.add_argument("--compare", default=None,
                        help="Model comparison JSON path")
    parser.add_argument("--output", required=True, help="Output .md path")
    args = parser.parse_args()

    if args.compare:
        with open(args.compare, encoding="utf-8") as f:
            comparison = json.load(f)
        generate_comparison_report(comparison, Path(args.output))
    elif args.input:
        with open(args.input, encoding="utf-8") as f:
            analysis = json.load(f)
        generate_report(analysis, Path(args.output))
    else:
        print("Provide either --input or --compare.")


if __name__ == "__main__":
    main()
