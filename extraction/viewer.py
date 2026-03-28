"""
extraction/viewer.py

Interactive viewer for raw CDM candidates.
Prints each candidate with full context and lets you tag it
as 'keep', 'skip', or 'maybe'.

Usage:
    python extraction/viewer.py                          # view all repos
    python extraction/viewer.py --repo flask            # single repo
    python extraction/viewer.py --repo flask --limit 5  # first 5 only
    python extraction/viewer.py --tagged                 # show tagged summary

Tags are saved to data/tasks/raw/<repo>_tagged.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

REPO_BASE = Path("data/repos")
RAW_DIR = Path("data/tasks/raw")

# ANSI colours for terminal output
_R = "\033[0m"
_BOLD = "\033[1m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_RED = "\033[31m"
_DIM = "\033[2m"


def _git_show_stat(repo_path: Path, sha: str) -> str:
    r = subprocess.run(
        ["git", "show", "--stat", sha],
        cwd=str(repo_path),
        capture_output=True, text=True,
    )
    return r.stdout[:2000]


def _print_candidate(idx: int, total: int, c: dict) -> None:
    cdm = c["cdm"]
    repo_path = REPO_BASE / c["repo_id"]

    print(f"\n{_BOLD}{'─'*70}{_R}")
    print(
        f"{_BOLD}[{idx+1}/{total}] {c['repo_id']} | {c['language']}{_R}  "
        f"sha={_CYAN}{c['sha'][:10]}{_R}"
    )
    print(f"{_BOLD}Message:{_R} {c['message']}")
    print()
    print(f"{_BOLD}Changed files ({len(c['changed_files'])}):{_R}")
    for f in c["changed_files"]:
        print(f"  {_DIM}{f}{_R}")
    print()
    print(f"{_BOLD}Required context ({len(cdm['required_context_files'])}):{_R}")
    for rf in cdm["required_context_files"]:
        # Mark if constraint-bearing
        cb_marker = ""
        for d in cdm["required_context_details"]:
            if d["file"] == rf and d["is_constraint_bearing"]:
                cb_marker = f" {_GREEN}[{d['constraint_type']}]{_R}"
        print(f"  {_CYAN}{rf}{_R}{cb_marker}")
        # Print the symbols used from this file
        for d in cdm["required_context_details"]:
            if d["file"] == rf:
                syms = ", ".join(d["symbols_used"][:8])
                if len(d["symbols_used"]) > 8:
                    syms += f" … +{len(d['symbols_used'])-8}"
                print(f"    symbols: {_DIM}{syms}{_R}")
    print()
    print(
        f"  hops={_YELLOW}{cdm['context_distance_hops']}{_R}  "
        f"irr={_YELLOW}{cdm['irreducibility_score']:.2f}{_R}  "
        f"cb_files={_YELLOW}{cdm['constraint_bearing_files']}{_R}"
    )
    print(f"  constraint_types: {cdm['constraint_types_found']}")
    print()
    print(f"{_BOLD}Diff preview:{_R}")
    for line in c["diff_preview"][:600].splitlines():
        if line.startswith("+++") or line.startswith("---"):
            print(f"  {_BOLD}{line}{_R}")
        elif line.startswith("+"):
            print(f"  {_GREEN}{line}{_R}")
        elif line.startswith("-"):
            print(f"  {_RED}{line}{_R}")
        else:
            print(f"  {_DIM}{line}{_R}")

    print()
    print(f"{_BOLD}git show --stat:{_R}")
    print(_DIM + _git_show_stat(repo_path, c["sha"])[:800] + _R)


def view_candidates(
    repo_filter: str | None = None,
    limit: int | None = None,
    interactive: bool = True,
) -> None:
    candidates_by_repo: dict[str, list[dict]] = {}

    if repo_filter:
        path = RAW_DIR / f"{repo_filter}_candidates.json"
        if path.exists():
            candidates_by_repo[repo_filter] = json.loads(path.read_text())
    else:
        for p in sorted(RAW_DIR.glob("*_candidates.json")):
            repo_id = p.stem.replace("_candidates", "")
            candidates_by_repo[repo_id] = json.loads(p.read_text())

    all_candidates = []
    for repo_id, cs in candidates_by_repo.items():
        for c in cs:
            if "repo_id" not in c:
                c["repo_id"] = repo_id
            all_candidates.append(c)

    if not all_candidates:
        print("No candidates found. Run extraction/git_miner.py first.")
        return

    if limit:
        all_candidates = all_candidates[:limit]

    tags: dict[str, str] = {}   # sha → tag

    for i, candidate in enumerate(all_candidates):
        _print_candidate(i, len(all_candidates), candidate)

        if interactive:
            while True:
                raw = input(
                    f"\n  {_BOLD}Tag this candidate? "
                    f"[k=keep / s=skip / m=maybe / q=quit / Enter=skip]:{_R} "
                ).strip().lower()
                if raw in ("k", "keep"):
                    tags[candidate["sha"]] = "keep"
                    print(f"  {_GREEN}✓ Tagged: keep{_R}")
                    break
                elif raw in ("s", "skip", ""):
                    tags[candidate["sha"]] = "skip"
                    break
                elif raw in ("m", "maybe"):
                    tags[candidate["sha"]] = "maybe"
                    print(f"  {_YELLOW}? Tagged: maybe{_R}")
                    break
                elif raw in ("q", "quit"):
                    print("Quitting early.")
                    _save_tags(tags, all_candidates)
                    return
                else:
                    print("  Please enter k, s, m, or q.")

    _save_tags(tags, all_candidates)


def _save_tags(tags: dict[str, str], candidates: list[dict]) -> None:
    if not tags:
        return

    tagged = []
    for c in candidates:
        tag = tags.get(c["sha"], "unreviewed")
        tagged.append({**c, "tag": tag})

    out_path = RAW_DIR / "tagged_candidates.json"
    out_path.write_text(json.dumps(tagged, indent=2))
    print(f"\n{_BOLD}Tags saved → {out_path}{_R}")

    # Summary
    keep = sum(1 for t in tagged if t["tag"] == "keep")
    maybe = sum(1 for t in tagged if t["tag"] == "maybe")
    skip = sum(1 for t in tagged if t["tag"] == "skip")
    unreviewed = sum(1 for t in tagged if t["tag"] == "unreviewed")
    print(f"  keep={_GREEN}{keep}{_R}  maybe={_YELLOW}{maybe}{_R}  "
          f"skip={skip}  unreviewed={unreviewed}")


def show_tagged_summary() -> None:
    path = RAW_DIR / "tagged_candidates.json"
    if not path.exists():
        print("No tagged file found. Run the viewer first.")
        return
    tagged = json.loads(path.read_text())
    keep = [t for t in tagged if t["tag"] == "keep"]
    maybe = [t for t in tagged if t["tag"] == "maybe"]
    print(f"\n{_BOLD}Tagged candidates summary:{_R}")
    print(f"  Total: {len(tagged)}  keep={len(keep)}  maybe={len(maybe)}")
    print(f"\n{_BOLD}Keep:{_R}")
    for t in keep:
        print(
            f"  {t['repo_id']} | {t['sha'][:10]} | {t['message'][:55]}\n"
            f"    required: {t['cdm']['required_context_files']}\n"
            f"    irr={t['cdm']['irreducibility_score']:.2f}"
        )
    print(f"\n{_BOLD}Maybe:{_R}")
    for t in maybe:
        print(
            f"  {t['repo_id']} | {t['sha'][:10]} | {t['message'][:55]}\n"
            f"    required: {t['cdm']['required_context_files']}\n"
            f"    irr={t['cdm']['irreducibility_score']:.2f}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="View and tag CDM candidates")
    parser.add_argument("--repo", help="Filter to one repo (flask/gin/typescript)")
    parser.add_argument("--limit", type=int, help="Max candidates to show")
    parser.add_argument(
        "--tagged",
        action="store_true",
        help="Show summary of already-tagged candidates",
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="Print all candidates without prompting for tags",
    )
    args = parser.parse_args()

    if args.tagged:
        show_tagged_summary()
    else:
        view_candidates(
            repo_filter=args.repo,
            limit=args.limit,
            interactive=not args.no_interactive,
        )