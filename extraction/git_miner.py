"""
extraction/git_miner.py  (Day 2 — fixed)

Fixes:
1. Passes `subtree` from REPO_CONFIGS to ContextDependencyMapper.
2. TypeScript: min_files=1 (many valuable compiler commits touch 1-2 files),
   and excludes .d.ts from source file counting.
3. TypeScript debug=True so we can see rejections.
4. _is_test_file handles all three language test patterns correctly.
5. Required context is sanitized (test files stripped) before validity check.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Generator, Optional

from cdm.mapper import ContextDependencyMapper

# ── Config ─────────────────────────────────────────────────────────────────────

REPO_BASE = Path("data/repos")
OUTPUT_DIR = Path("data/tasks/raw")

REPO_CONFIGS = [
    {
        "id": "flask",
        "path": REPO_BASE / "flask",
        "language": "python",
        "subtree": None,
        "min_files": 2,
        "n_commits": 200,
        "min_irr": 0.18,
        "debug": False,
    },
    {
        "id": "gin",
        "path": REPO_BASE / "gin",
        "language": "go",
        "subtree": None,
        "min_files": 2,
        "n_commits": 300,
        "min_irr": 0.15,
        "debug": True,
    },
    {
        "id": "typescript",
        "path": REPO_BASE / "typescript",
        "language": "typescript",
        "subtree": "src/compiler",   # only collect src/compiler as graph nodes
        "min_files": 1,              # 1 changed compiler file is enough
        "n_commits": 300,
        "min_irr": 0.18,
        "debug": True,
    },
]

DEBUG_ALL = os.environ.get("DEBUG_MINING", "").strip() == "1"


# ── Test-file predicates ───────────────────────────────────────────────────────

_TEST_DIRS = {
    "tests", "test", "__tests__", "testdata",
    "testrunner", "unittests", "harness",
    "e2e", "fixtures", "mocks", "stubs",
}

_TEST_FILENAME_FRAGMENTS = (
    "test_", "_test.", "_spec.", ".test.", ".spec.",
)

_NON_SOURCE_EXTS = (
    ".rst", ".md", ".txt", ".yaml", ".yml",
    ".toml", ".cfg", ".ini", ".lock", ".sum",
    ".d.ts",   # TypeScript declaration files — not source
)

_TS_TEST_SUBSTRINGS = (
    "testRunner", "unittests", "src/harness", "testrunner",
)


def _is_test_file(filepath: str) -> bool:
    p = filepath.replace("\\", "/")
    p_lower = p.lower()
    parts = p_lower.split("/")

    # Check directory segments (not the filename)
    for part in parts[:-1]:
        if part in _TEST_DIRS:
            return True

    # Filename patterns
    filename = parts[-1]
    for frag in _TEST_FILENAME_FRAGMENTS:
        if frag in filename:
            return True
    if filename.endswith("_test.go"):
        return True

    # TS-specific substrings (mixed-case matters)
    for substr in _TS_TEST_SUBSTRINGS:
        if substr in p:
            return True

    # Non-source extensions (includes .d.ts)
    for ext in _NON_SOURCE_EXTS:
        if p_lower.endswith(ext):
            return True

    return False


def _is_source_file(filepath: str, language: str) -> bool:
    if _is_test_file(filepath):
        return False
    p = filepath.lower()
    if language == "python":
        return p.endswith(".py")
    if language == "typescript":
        # Exclude .d.ts — they're type declarations, not editable source
        return (p.endswith(".ts") or p.endswith(".tsx")) and not p.endswith(".d.ts")
    if language == "go":
        return p.endswith(".go")
    return False


# ── Git helpers ────────────────────────────────────────────────────────────────

def _run_git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git"] + args, cwd=str(cwd), capture_output=True, text=True,
    )
    return result.stdout


def iter_commits(
    repo_path: Path,
    language: str,
    min_files: int = 2,
    n: int = 200,
    subtree: Optional[str] = None,
) -> Generator[dict, None, None]:
    """Yield commits that changed >= min_files non-test source files.
    If subtree is set, only count files inside that subdirectory.
    """
    log = _run_git(["log", "--format=%H|%s", f"-{n}"], repo_path)
    for line in log.strip().split("\n"):
        if "|" not in line:
            continue
        sha, message = line.split("|", 1)
        sha = sha.strip()
        if not sha:
            continue

        files_out = _run_git(
            ["diff", "--name-only", f"{sha}^", sha], repo_path
        ).strip().split("\n")

        source_files = [
            f.strip() for f in files_out
            if f.strip() and _is_source_file(f.strip(), language)
        ]

        # If subtree restriction: only count files in that subtree
        if subtree:
            source_files = [f for f in source_files if f.startswith(subtree)]

        if len(source_files) >= min_files:
            yield {"sha": sha, "message": message.strip(),
                   "changed_files": source_files}


def get_diff(repo_path: Path, sha: str) -> str:
    return _run_git(["diff", f"{sha}^", sha], repo_path)


# ── Mining ─────────────────────────────────────────────────────────────────────

def mine_repo(config: dict) -> list[dict]:
    repo_id = config["id"]
    repo_path = config["path"]
    language = config["language"]
    min_irr = config["min_irr"]
    subtree = config.get("subtree")
    debug = config.get("debug", False) or DEBUG_ALL

    print(f"\n{'='*60}")
    print(f"Mining {repo_id} ({language})  [min_irr={min_irr}]")
    print(f"{'='*60}")

    try:
        mapper = ContextDependencyMapper(str(repo_path), language, subtree=subtree)
    except Exception as e:
        print(f"  ERROR building CDM: {e}")
        return []

    print(f"  Import graph: {mapper.G.number_of_nodes()} nodes, "
          f"{mapper.G.number_of_edges()} edges")

    candidates = []
    total_checked = 0

    for commit in iter_commits(
        repo_path, language,
        min_files=config["min_files"],
        n=config["n_commits"],
        subtree=subtree,
    ):
        total_checked += 1
        diff = get_diff(repo_path, commit["sha"])
        if not diff:
            continue

        try:
            cdm_result = mapper.analyze(
                commit["changed_files"], diff, min_irr=min_irr
            )
        except Exception as e:
            if debug:
                print(f"  CDM ERROR [{commit['sha'][:8]}]: {e}")
            continue

        # Sanitise required context: remove test files
        clean_required = [f for f in cdm_result.required_context_files
                          if not _is_test_file(f)]
        removed_count = len(cdm_result.required_context_files) - len(clean_required)
        cdm_result.required_context_files = clean_required
        cdm_result.required_context_details = [
            d for d in cdm_result.required_context_details if not _is_test_file(d.file)
        ]
        cdm_result.constraint_bearing_files = [
            f for f in cdm_result.constraint_bearing_files if not _is_test_file(f)
        ]

        if not cdm_result.required_context_files:
            if debug:
                print(f"  SKIP [{commit['sha'][:8]}] {commit['message'][:50]}"
                      f"\n    → no required context"
                      + (f" (removed {removed_count} test files)" if removed_count else ""))
            continue

        if not cdm_result.is_valid_task(min_irr=min_irr):
            if debug:
                print(f"  SKIP [{commit['sha'][:8]}] {commit['message'][:50]}"
                      f"\n    irr={cdm_result.irreducibility_score:.3f} < {min_irr}"
                      f"  hops={cdm_result.context_distance_hops}"
                      f"\n    required={cdm_result.required_context_files}"
                      f"\n    cb={cdm_result.constraint_bearing_files}")
            continue

        candidate = {
            "repo_id": repo_id,
            "language": language,
            "sha": commit["sha"],
            "message": commit["message"],
            "changed_files": commit["changed_files"],
            "cdm": cdm_result.to_dict(),
            "diff_preview": diff[:1200],
        }
        candidates.append(candidate)
        print(
            f"  FOUND [{commit['sha'][:8]}] {commit['message'][:55]}\n"
            f"    changed : {commit['changed_files']}\n"
            f"    required: {cdm_result.required_context_files}\n"
            f"    hops={cdm_result.context_distance_hops}  "
            f"irr={cdm_result.irreducibility_score:.2f}  "
            f"cb={cdm_result.constraint_bearing_files}"
        )

    print(f"\n  Checked {total_checked} commits → {len(candidates)} candidates")
    return candidates


def run_all() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    all_candidates: list[dict] = []

    for config in REPO_CONFIGS:
        if not config["path"].exists():
            print(f"  SKIP {config['id']} — repo not found at {config['path']}")
            continue
        candidates = mine_repo(config)
        all_candidates.extend(candidates)
        out_path = OUTPUT_DIR / f"{config['id']}_candidates.json"
        out_path.write_text(json.dumps(candidates, indent=2))
        print(f"  Saved {len(candidates)} candidates → {out_path}")

    combined_path = OUTPUT_DIR / "all_candidates.json"
    combined_path.write_text(json.dumps(all_candidates, indent=2))
    print(f"\n{'='*60}")
    print(f"TOTAL candidates: {len(all_candidates)}")
    print(f"Combined → {combined_path}")


if __name__ == "__main__":
    run_all()