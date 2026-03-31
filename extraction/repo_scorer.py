"""
extraction/repo_scorer.py

Scores candidate repositories before committing to full mining.

WHY THIS EXISTS
---------------
Mining is expensive: building the import graph for a large TypeScript repo
takes minutes. Before running the full CDM on a new repository, this script
runs a quick pre-screening to estimate whether the repo will yield high-quality
candidates and flags repositories that are likely to be barren.

SCORING COMPONENTS
------------------
  ImportDensity     — edge/node ratio of the import graph.
                      Sparse graphs (< 1.5 edges/node) rarely produce
                      multi-hop dependencies.  Dense graphs (> 5.0) often
                      have everything depending on everything (barrel hell)
                      which also yields low-quality candidates.
                      Sweet spot: 2.0 – 4.5.

  TestCoverage      — fraction of source files with a corresponding test file.
                      Low coverage (< 20%) means tasks are hard to verify.

  InterfaceCount    — number of exported interface / ABC / Protocol definitions.
                      More interfaces → more structural coupling opportunities
                      → more structural signal in the CDM.

  HistoryDepth      — number of qualifying commits in the last 12 months
                      (multi-file, non-merge, non-chore).  Repos with < 10
                      qualifying commits cannot produce enough candidates.

  CommitQuality     — fraction of qualifying commits with descriptive messages
                      (> 30 chars, not just "fix", "typo", "bump").

  ContaminationRisk — LOW if SHA is post-training-cutoff, MEDIUM if recent,
                      HIGH if likely in training data.

USAGE:
  python extraction/repo_scorer.py --repo data/repos/gin --language go
  python extraction/repo_scorer.py --all  # scores all repos in repo_manifest.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


REPO_BASE     = Path("data/repos")
MANIFEST_PATH = Path("config/repo_manifest.json")

                           
MIN_DENSITY = 1.5
MAX_DENSITY = 5.5

                                              
MIN_COMMITS = 10


@dataclass
class RepoScore:
    repo_id:           str
    language:          str
    n_nodes:           int
    n_edges:           int
    import_density:    float
    n_interfaces:      int
    test_coverage_pct: float
    qualifying_commits: int
    commit_quality:    float
    contamination_risk: str                        
    total_score:       float         
    recommendation:    str                                

    def as_dict(self) -> dict:
        return {
            "repo_id": self.repo_id,
            "language": self.language,
            "n_nodes": self.n_nodes,
            "n_edges": self.n_edges,
            "import_density": round(self.import_density, 2),
            "n_interfaces": self.n_interfaces,
            "test_coverage_pct": round(self.test_coverage_pct, 1),
            "qualifying_commits": self.qualifying_commits,
            "commit_quality": round(self.commit_quality, 2),
            "contamination_risk": self.contamination_risk,
            "total_score": round(self.total_score, 1),
            "recommendation": self.recommendation,
        }


def _count_interfaces(repo_path: Path, language: str) -> int:
    """Count exported interface/ABC/Protocol definitions via grep."""
    patterns = {
        "python": r"class\s+\w+.*(?:ABC|Protocol|TypedDict|ABCMeta)\b",
        "typescript": r"^export\s+(?:abstract\s+)?interface\s+",
        "go": r"^type\s+[A-Z]\w+\s+interface\s*\{",
    }
    pattern = patterns.get(language, "")
    if not pattern:
        return 0
    ext = {"python": "*.py", "typescript": "*.ts", "go": "*.go"}.get(language, "*")
    result = subprocess.run(
        ["grep", "-r", "--include", ext, "-lE", pattern, str(repo_path)],
        capture_output=True, text=True
    )
    return len(result.stdout.strip().split("\n")) if result.stdout.strip() else 0


def _test_coverage_estimate(repo_path: Path, language: str) -> float:
    """Estimate test coverage as fraction of source files with a test counterpart."""
    ext = {"python": "*.py", "typescript": "*.ts", "go": "*.go"}.get(language, "*.py")
    all_src = [
        f for f in repo_path.rglob(ext)
        if "test" not in str(f).lower() and "vendor" not in str(f)
    ]
    test_src = [
        f for f in repo_path.rglob(ext)
        if ("test" in f.name.lower() or "spec" in f.name.lower()
            or f.name.endswith("_test.go"))
    ]
    if not all_src:
        return 0.0
    return min(1.0, len(test_src) / len(all_src)) * 100


def _qualifying_commits(repo_path: Path, n: int = 200) -> tuple[int, float]:
    """
    Count commits that qualify for mining (multi-file, non-merge, non-trivial).
    Returns (count, commit_quality_fraction).
    """
    log = subprocess.run(
        ["git", "log", "--format=%s", f"-{n}", "--no-merges"],
        cwd=str(repo_path), capture_output=True, text=True
    ).stdout.strip().split("\n")

    trivial = {"fix", "typo", "bump", "update", "chore", "merge", "wip", "revert"}
    quality = sum(
        1 for msg in log
        if len(msg) > 30 and not any(msg.lower().startswith(t) for t in trivial)
    )
    return len(log), quality / max(1, len(log))


def _contamination_risk(repo_path: Path) -> str:
    """
    Estimate contamination risk based on how recently the repository was active.
    Repos with commits after 2025-06 are LOW risk for current frontier models.
    """
    latest = subprocess.run(
        ["git", "log", "-1", "--format=%ci"],
        cwd=str(repo_path), capture_output=True, text=True
    ).stdout.strip()
    if not latest:
        return "UNKNOWN"
    year = int(latest[:4]) if latest else 2024
    month = int(latest[5:7]) if len(latest) >= 7 else 1
    if year > 2025 or (year == 2025 and month >= 9):
        return "LOW"
    if year == 2025:
        return "MEDIUM"
    return "HIGH"


def score_repo(repo_id: str, language: str, repo_path: Path) -> RepoScore:
    """Compute all scoring components and produce a RepoScore."""
    print(f"  Scoring {repo_id} ({language}) …", end=" ", flush=True)

                                                
    try:
        from cdm.mapper import ContextDependencyMapper
        mapper = ContextDependencyMapper(str(repo_path), language)
        G = mapper.G
        n_nodes = G.number_of_nodes()
        n_edges = G.number_of_edges()
    except Exception:
        n_nodes, n_edges = 0, 0

    density = n_edges / max(1, n_nodes)

    n_ifaces      = _count_interfaces(repo_path, language)
    test_pct      = _test_coverage_estimate(repo_path, language)
    n_commits, cq = _qualifying_commits(repo_path)
    contam_risk   = _contamination_risk(repo_path)

                                   
                                                          
    if MIN_DENSITY <= density <= MAX_DENSITY:
        density_score = 100.0
    elif density < MIN_DENSITY:
        density_score = max(0, density / MIN_DENSITY * 100)
    else:
        density_score = max(0, 100 - (density - MAX_DENSITY) * 15)

    interface_score = min(100, n_ifaces * 5)                             
    test_score      = min(100, test_pct * 1.5)                          
    commit_score    = min(100, n_commits * 3)                          
    quality_score   = cq * 100
    contam_score    = {"LOW": 100, "MEDIUM": 60, "HIGH": 20}.get(contam_risk, 40)

    total = (
        0.30 * density_score
        + 0.20 * interface_score
        + 0.15 * test_score
        + 0.15 * commit_score
        + 0.10 * quality_score
        + 0.10 * contam_score
    )

    recommendation = (
        "INCLUDE" if total >= 65 else
        "REVIEW"  if total >= 40 else
        "EXCLUDE"
    )

    print(f"score={total:.1f} → {recommendation}")
    return RepoScore(
        repo_id=repo_id, language=language,
        n_nodes=n_nodes, n_edges=n_edges,
        import_density=round(density, 2),
        n_interfaces=n_ifaces,
        test_coverage_pct=test_pct,
        qualifying_commits=n_commits,
        commit_quality=cq,
        contamination_risk=contam_risk,
        total_score=total,
        recommendation=recommendation,
    )


def score_all(manifest_path: Path = MANIFEST_PATH) -> list[RepoScore]:
    if not manifest_path.exists():
        print(f"Manifest not found: {manifest_path}")
        return []

    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    repos = manifest.get("repos", manifest) if isinstance(manifest, dict) else manifest
    results: list[RepoScore] = []

    for repo in repos:
        repo_id  = repo.get("id") or repo.get("repo_id", "")
        language = repo.get("language", "python")
        repo_path = REPO_BASE / repo_id
        if not repo_path.exists():
            print(f"  SKIP {repo_id} — not found at {repo_path}")
            continue
        results.append(score_repo(repo_id, language, repo_path))

                         
    print(f"\n{'Repo':<25} {'Score':>7} {'Density':>8} {'Ifaces':>7} "
          f"{'Tests%':>7} {'Commits':>8} {'Risk':<8} {'Rec'}")
    print("─" * 80)
    for r in sorted(results, key=lambda x: -x.total_score):
        print(f"  {r.repo_id:<23} {r.total_score:>6.1f} {r.import_density:>8.2f} "
              f"{r.n_interfaces:>7} {r.test_coverage_pct:>6.1f}% {r.qualifying_commits:>8} "
              f"  {r.contamination_risk:<8} {r.recommendation}")

    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=None,
                        help="Single repo path to score")
    parser.add_argument("--language", default="python",
                        help="Language of the repo")
    parser.add_argument("--all", action="store_true",
                        help="Score all repos in repo_manifest.json")
    parser.add_argument("--output", default=None,
                        help="Write scores to JSON file")
    args = parser.parse_args()

    if args.all:
        scores = score_all()
    elif args.repo:
        repo_path = Path(args.repo)
        repo_id   = repo_path.name
        scores = [score_repo(repo_id, args.language, repo_path)]
    else:
        print("Use --repo <path> or --all")
        return

    if args.output and scores:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps([s.as_dict() for s in scores], indent=2))
        print(f"\nScores written → {out}")


if __name__ == "__main__":
    main()