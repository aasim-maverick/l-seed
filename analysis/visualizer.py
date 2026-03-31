"""
analysis/visualizer.py

Produces figures from LongContext-Bench analysis JSON files.

Requires: matplotlib, seaborn, numpy.

USAGE:
  python analysis/visualizer.py \\
      --input data/results/gemini-2.5-flash/2026-03-31_analysis.json \\
      --output data/results/figures/

  # Compare multiple models (pass glob or multiple --input flags)
  python analysis/visualizer.py \\
      --input data/results/*/2026-03-31_analysis.json \\
      --output data/results/figures/
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


METRIC_LABELS = {
    "icu": "ICU (Information Coverage Utilization)",
    "ccs": "CCS (Context Coverage Score)",
    "pes": "PES (Path Efficiency Score)",
    "ter": "TER (Tool Efficiency Ratio)",
    "cfrd": "CFRD (Cross-File Reasoning Depth)",
    "rfs":  "RFS (Reasoning Forcing Score)",
    "irr":  "IRR (Irreducibility Score)",
}

DIFFICULTY_ORDER = ["easy", "medium", "hard"]
PASS_COLOR  = "#2ecc71"
FAIL_COLOR  = "#e74c3c"
METRIC_COLORS = ["#3498db", "#9b59b6", "#e67e22", "#1abc9c"]


def _require_mpl() -> None:
    if not HAS_MPL:
        raise ImportError("matplotlib and numpy required: pip install matplotlib numpy seaborn")


def plot_per_task_metrics(analysis: dict, output_dir: Path) -> None:
    """
    Horizontal bar chart: one row per task, four metric bars side by side.
    Tasks sorted by difficulty then task_id.  Pass/fail indicated by row colour.
    """
    _require_mpl()
    by_task = sorted(
        analysis.get("by_task", []),
        key=lambda x: (DIFFICULTY_ORDER.index(x.get("difficulty_level", "hard"))
                       if x.get("difficulty_level") in DIFFICULTY_ORDER else 99,
                       x.get("task_id", ""))
    )
    if not by_task:
        return

    metrics = ["icu", "ccs", "pes", "ter"]
    n = len(by_task)
    fig, ax = plt.subplots(figsize=(12, max(4, n * 0.55)))

    bar_height = 0.18
    y_positions = np.arange(n)

    for i, (metric, color) in enumerate(zip(metrics, METRIC_COLORS)):
        values = [t.get(metric, 0) for t in by_task]
        offset = (i - 1.5) * bar_height
        bars = ax.barh(y_positions + offset, values, bar_height * 0.9,
                       color=color, alpha=0.85, label=metric.upper())

    task_labels = [t["task_id"] for t in by_task]
    row_colors  = [PASS_COLOR if t.get("task_success") else FAIL_COLOR
                   for t in by_task]
    ax.set_yticks(y_positions)
    ax.set_yticklabels(task_labels, fontsize=9)
    for tick, color in zip(ax.get_yticklabels(), row_colors):
        tick.set_color(color)

    ax.set_xlim(0, 1.05)
    ax.set_xlabel("Score")
    ax.set_title(f"Per-Task Metrics — {analysis.get('model', 'unknown')} "
                 f"(Pass: {analysis.get('pass_rate', 0):.0%})")
    ax.legend(loc="lower right", fontsize=8)
    ax.axvline(x=0.5, color="gray", linestyle="--", alpha=0.4, linewidth=0.8)

    pass_patch = mpatches.Patch(color=PASS_COLOR, label="Pass")
    fail_patch = mpatches.Patch(color=FAIL_COLOR, label="Fail")
    ax.legend(handles=[pass_patch, fail_patch] +
              [mpatches.Patch(color=c, label=m.upper())
               for m, c in zip(metrics, METRIC_COLORS)],
              loc="lower right", fontsize=8)

    plt.tight_layout()
    out = output_dir / f"per_task_metrics_{analysis.get('model', 'model').replace('/', '-')}.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved → {out}")


def plot_failure_distribution(analysis: dict, output_dir: Path) -> None:
    """Pie chart of failure mode distribution."""
    _require_mpl()
    fail_dist = analysis.get("failure_distribution", {})
    if not fail_dist:
        return

    labels = list(fail_dist.keys())
    sizes  = list(fail_dist.values())
    colors = plt.cm.Set3(np.linspace(0, 1, len(labels)))                

    fig, ax = plt.subplots(figsize=(8, 5))
    wedges, texts, autotexts = ax.pie(
        sizes, labels=None, autopct="%1.0f%%",
        colors=colors, startangle=90,
        wedgeprops={"edgecolor": "white", "linewidth": 1},
    )
    ax.legend(wedges, [l.replace("_", " ") for l in labels],
              loc="center left", bbox_to_anchor=(1, 0, 0.5, 1), fontsize=9)
    ax.set_title(f"Failure Mode Distribution — {analysis.get('model', 'unknown')}")

    plt.tight_layout()
    out = output_dir / f"failure_dist_{analysis.get('model', 'model').replace('/', '-')}.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved → {out}")


def plot_icu_vs_rfs(analysis: dict, output_dir: Path) -> None:
    """
    Scatter plot: ICU (agent metric, y-axis) vs RFS (task metric, x-axis).
    Each point is a task run, coloured by pass/fail.
    This is the primary diagnostic plot: it shows whether hard tasks (high RFS)
    are harder for the agent (low ICU), and whether the agent's context
    retrieval quality (ICU) predicts task success.
    """
    _require_mpl()
    by_task    = analysis.get("by_task", [])
    task_mets  = analysis.get("task_metrics", {})

    points = []
    for t in by_task:
        tid  = t.get("task_id", "")
        rfs  = task_mets.get(tid, {}).get("rfs", t.get("rfs", 0))
        icu  = t.get("icu", 0)
        succ = t.get("task_success", False)
        points.append((rfs, icu, succ, tid))

    if not points:
        return

    fig, ax = plt.subplots(figsize=(7, 5))
    for rfs, icu, succ, tid in points:
        color  = PASS_COLOR if succ else FAIL_COLOR
        marker = "o" if succ else "x"
        ax.scatter(rfs, icu, c=color, marker=marker, s=100, zorder=5)
        ax.annotate(tid, (rfs, icu), fontsize=7,
                    xytext=(4, 4), textcoords="offset points", alpha=0.8)

    ax.set_xlabel("RFS (Reasoning Forcing Score) — task difficulty →")
    ax.set_ylabel("ICU (Information Coverage Utilization) — retrieval quality →")
    ax.set_title(f"ICU vs RFS — {analysis.get('model', 'unknown')}")
    ax.set_xlim(-0.05, 1.05)
    ax.set_ylim(-0.05, 1.05)
    ax.axhline(y=0.5, color="gray", linestyle="--", alpha=0.3)
    ax.axvline(x=0.5, color="gray", linestyle="--", alpha=0.3)

    pass_patch = mpatches.Patch(color=PASS_COLOR, label="Pass")
    fail_patch = mpatches.Patch(color=FAIL_COLOR, label="Fail")
    ax.legend(handles=[pass_patch, fail_patch])

    plt.tight_layout()
    out = output_dir / f"icu_vs_rfs_{analysis.get('model', 'model').replace('/', '-')}.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved → {out}")


def plot_model_comparison(analyses: list[dict], output_dir: Path) -> None:
    """
    Grouped bar chart comparing all four run metrics across models.
    """
    _require_mpl()
    if len(analyses) < 2:
        return

    models  = [a.get("model", f"model_{i}") for i, a in enumerate(analyses)]
    metrics = ["avg_icu", "avg_ccs", "avg_pes", "avg_ter"]
    labels  = ["ICU", "CCS", "PES", "TER"]

    x     = np.arange(len(labels))
    width = 0.8 / len(models)
    fig, ax = plt.subplots(figsize=(9, 5))

    for i, (model, a) in enumerate(zip(models, analyses)):
        vals = [a.get(m, 0) for m in metrics]
        offset = (i - len(models) / 2 + 0.5) * width
        ax.bar(x + offset, vals, width * 0.9, label=model, alpha=0.85)

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Score")
    ax.set_title("Model Comparison — LongContext-Bench Metrics")
    ax.legend()
    ax.axhline(y=0.5, color="gray", linestyle="--", alpha=0.3)

    plt.tight_layout()
    out = output_dir / "model_comparison.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved → {out}")


def generate_all_figures(analyses: list[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for a in analyses:
        plot_per_task_metrics(a, output_dir)
        plot_failure_distribution(a, output_dir)
        plot_icu_vs_rfs(a, output_dir)
    if len(analyses) > 1:
        plot_model_comparison(analyses, output_dir)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", nargs="+", required=True,
                        help="One or more analysis JSON files")
    parser.add_argument("--output", required=True,
                        help="Output directory for figures")
    args = parser.parse_args()

    analyses = []
    for p in args.input:
        for fp in Path(".").glob(p) if "*" in p else [Path(p)]:
            with open(fp, encoding="utf-8") as f:
                analyses.append(json.load(f))

    generate_all_figures(analyses, Path(args.output))


if __name__ == "__main__":
    main()