"""
extraction/task_validator.py

Validates a task JSON record before it is committed to data/tasks/validated/.

VALIDATION STAGES
-----------------
Stage 1 — Schema: required fields present, types correct, values in range.
Stage 2 — CDM coherence: required_context_files actually exist in the repo
          at the given commit SHA; changed_files exist at parent commit.
Stage 3 — Semantic: irreducibility_score above minimum; required context
          files are not a subset of changed files.
Stage 4 — Contamination hint: prompts the user to check whether the model
          can solve the task without tool access (manual step, not automated).

USAGE:
  python extraction/task_validator.py data/tasks/raw/flask_candidates.json
  python extraction/task_validator.py data/tasks/validated/flask-001.json --strict
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional


REPO_BASE = Path("data/repos")

REQUIRED_FIELDS = {
    "task_id": str,
    "repo_id": str,
    "repo_url": str,
    "commit_sha": str,
    "language": str,
    "task_category": str,
    "difficulty_level": str,
    "required_context_files": list,
    "changed_files": list,
    "prompt": str,
}

ALLOWED_CATEGORIES  = {"bug_investigation", "feature_implementation", "refactoring"}
ALLOWED_DIFFICULTIES = {"easy", "medium", "hard", "expert"}
ALLOWED_LANGUAGES    = {"python", "typescript", "go", "rust", "java", "cpp"}
MIN_IRREDUCIBILITY   = 0.15
MIN_PROMPT_CHARS     = 100


class ValidationError(Exception):
    pass


                                                  


def _git_file_exists(repo_path: Path, ref: str, filepath: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{ref}:{filepath}"],
        cwd=str(repo_path), capture_output=True,
    )
    return result.returncode == 0


def _validate_schema(task: dict) -> list[str]:
    errors: list[str] = []

    for field, expected_type in REQUIRED_FIELDS.items():
        if field not in task:
            errors.append(f"Missing required field: {field}")
        elif not isinstance(task[field], expected_type):
            errors.append(
                f"Field {field}: expected {expected_type.__name__}, "
                f"got {type(task[field]).__name__}"
            )

    if task.get("task_category") not in ALLOWED_CATEGORIES:
        errors.append(
            f"Invalid task_category: {task.get('task_category')}. "
            f"Allowed: {ALLOWED_CATEGORIES}"
        )

    if task.get("difficulty_level") not in ALLOWED_DIFFICULTIES:
        errors.append(
            f"Invalid difficulty_level: {task.get('difficulty_level')}. "
            f"Allowed: {ALLOWED_DIFFICULTIES}"
        )

    if task.get("language") not in ALLOWED_LANGUAGES:
        errors.append(
            f"Invalid language: {task.get('language')}. "
            f"Allowed: {ALLOWED_LANGUAGES}"
        )

    if len(task.get("prompt", "")) < MIN_PROMPT_CHARS:
        errors.append(
            f"Prompt too short ({len(task.get('prompt', ''))} chars). "
            f"Minimum: {MIN_PROMPT_CHARS}"
        )

    if not task.get("required_context_files"):
        errors.append("required_context_files is empty (every task needs at least one)")

    if not task.get("changed_files"):
        errors.append("changed_files is empty")

                                                                              
    req_set     = set(task.get("required_context_files", []))
    changed_set = set(task.get("changed_files", []))
    if req_set and req_set.issubset(changed_set):
        errors.append(
            "required_context_files is a subset of changed_files. "
            "Required context must include files NOT in the diff."
        )

    return errors


def _validate_cdm_coherence(task: dict) -> list[str]:
    errors: list[str] = []
    repo_id    = task.get("repo_id", "")
    commit_sha = task.get("commit_sha", "")
    repo_path  = REPO_BASE / repo_id

    if not repo_path.exists():
        errors.append(f"Repo not found at {repo_path} (run git clone first)")
        return errors

    parent_ref = f"{commit_sha}^"

    for filepath in task.get("changed_files", []):
        if not _git_file_exists(repo_path, parent_ref, filepath):
            errors.append(
                f"changed_file not found at parent commit: {filepath} "
                f"(maybe it was created in this commit — verify)"
            )

    for filepath in task.get("required_context_files", []):
        if not _git_file_exists(repo_path, commit_sha, filepath):
            errors.append(
                f"required_context_file not found at commit SHA: {filepath}"
            )

    return errors


def _validate_semantic(task: dict) -> list[str]:
    warnings: list[str] = []

    irr = task.get("irreducibility_score", task.get("cdm", {}).get("irreducibility_score", 0))
    if float(irr) < MIN_IRREDUCIBILITY:
        warnings.append(
            f"irreducibility_score ({irr:.3f}) below minimum ({MIN_IRREDUCIBILITY}). "
            "This task may be solvable without reading the required context."
        )

    diff_score = task.get("difficulty_score", 0)
    if diff_score == 0:
        warnings.append("difficulty_score is 0 (not computed). Consider running the CDM.")

    if not task.get("constraint_propagation_targets"):
        warnings.append(
            "constraint_propagation_targets is empty. "
            "This weakens CPC checking in the eval template."
        )

    return warnings


def validate_task(task_path: Path, strict: bool = False) -> bool:
    with open(task_path, encoding="utf-8") as f:
        task = json.load(f)

    print(f"\nValidating {task_path.name} …")
    all_errors: list[str] = []
    all_warnings: list[str] = []

                     
    schema_errors = _validate_schema(task)
    all_errors.extend(schema_errors)
    if schema_errors:
        print(f"  [SCHEMA] {len(schema_errors)} error(s)")
        for e in schema_errors:
            print(f"    ✗ {e}")
    else:
        print("  [SCHEMA] OK")

                                                 
    cdm_errors = _validate_cdm_coherence(task)
    all_errors.extend(cdm_errors)
    if cdm_errors:
        print(f"  [CDM] {len(cdm_errors)} error(s)")
        for e in cdm_errors:
            print(f"    ✗ {e}")
    else:
        print("  [CDM] OK")

                              
    warnings = _validate_semantic(task)
    all_warnings.extend(warnings)
    if warnings:
        print(f"  [SEMANTIC] {len(warnings)} warning(s)")
        for w in warnings:
            print(f"    ⚠ {w}")
    else:
        print("  [SEMANTIC] OK")

                                 
    print("  [CONTAMINATION] Manual check required:")
    print(f"    Test: present prompt to {task.get('repo_id', '?')} model WITHOUT tool access.")
    print("    If model produces correct patch → task is contaminated, discard.")

    passed = not all_errors and not (strict and all_warnings)
    status = "✅ VALID" if passed else "❌ INVALID"
    print(f"\n  {status}  ({len(all_errors)} errors, {len(all_warnings)} warnings)")
    return passed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate task JSON records for LongContext-Bench"
    )
    parser.add_argument("paths", nargs="+", help="Task JSON file(s) to validate")
    parser.add_argument("--strict", action="store_true",
                        help="Treat warnings as errors")
    args = parser.parse_args()

    all_passed = True
    for path_str in args.paths:
        for p in Path(".").glob(path_str) if "*" in path_str else [Path(path_str)]:
            if not validate_task(p, strict=args.strict):
                all_passed = False

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()