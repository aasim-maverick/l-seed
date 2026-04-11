"""
codegen/run_codegen.py

Usage:
    python codegen/run_codegen.py                            # all tasks
    python codegen/run_codegen.py --dry-run                  # no writes
    python codegen/run_codegen.py --tasks flask-001 gin-002  # specific tasks

After generation:
    cd ../gemini-cli && npx tsc --noEmit evals/l-seed/*.eval.ts
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from codegen.eval_generator import EvalGenerator


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--tasks-dir', default='data/tasks/validated')
    parser.add_argument('--gemini-cli', default='../gemini-cli')
    parser.add_argument('--repo-base', default='data/repos')
    parser.add_argument('--tasks', nargs='+', metavar='TASK_ID')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    for label, p in [
        ('tasks-dir', args.tasks_dir),
        ('gemini-cli', args.gemini_cli),
        ('repo-base', args.repo_base),
    ]:
        if not Path(p).exists():
            print(f'ERROR: {label} not found: {p}', file=sys.stderr)
            sys.exit(1)

    gen = EvalGenerator(
        gemini_cli_path=args.gemini_cli,
        repo_base=args.repo_base,
    )

    if args.dry_run:
        print('[DRY RUN] No files will be written.\n')

    generated = gen.generate_all(
        task_dir=args.tasks_dir,
        dry_run=args.dry_run,
        task_ids=args.tasks,
    )

    if generated and not args.dry_run:
        cli = Path(args.gemini_cli)
        print(f'\nType-check:')
        print(f'  cd {cli} && npx tsc --noEmit evals/l-seed/*.eval.ts')
        print(f'\nRun single task:')
        print(f'  cd {cli} && RUN_EVALS=1 GEMINI_MODEL=gemini-2.5-flash \\')
        print(f'    L_SEED_LOG=../<l-seed-repo>/data/results/run1.jsonl \\')
        print(f'    npx vitest run evals/l-seed/flask-001.eval.ts')


if __name__ == '__main__':
    main()
