"""
codegen/run_codegen.py

Runner script: generates eval .ts files for all validated task JSONs.

Usage:
    # Generate all tasks
    python codegen/run_codegen.py

    # Dry run (check file sizes and truncation without writing)
    python codegen/run_codegen.py --dry-run

    # Generate specific tasks only
    python codegen/run_codegen.py --tasks flask-001 flask-002 gin-001

    # Override paths
    python codegen/run_codegen.py \\
        --tasks-dir data/tasks/validated \\
        --gemini-cli ../gemini-cli \\
        --repo-base data/repos

After generation, type-check with:
    cd ../gemini-cli && npx tsc --noEmit evals/longcontext/*.eval.ts
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from the longcontext-bench root
sys.path.insert(0, str(Path(__file__).parent.parent))

from codegen.eval_generator import EvalGenerator


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Generate Gemini CLI eval files from validated task JSONs'
    )
    parser.add_argument(
        '--tasks-dir',
        default='data/tasks/validated',
        help='Directory containing validated task JSON files (default: data/tasks/validated)',
    )
    parser.add_argument(
        '--gemini-cli',
        default='../gemini-cli',
        help='Path to Gemini CLI repo fork (default: ../gemini-cli)',
    )
    parser.add_argument(
        '--repo-base',
        default='data/repos',
        help='Directory containing cloned repos (default: data/repos)',
    )
    parser.add_argument(
        '--tasks',
        nargs='+',
        metavar='TASK_ID',
        help='Generate only these task IDs (e.g. flask-001 gin-002)',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be generated without writing files',
    )
    args = parser.parse_args()

    tasks_dir = Path(args.tasks_dir)
    gemini_cli = Path(args.gemini_cli)
    repo_base = Path(args.repo_base)

    # Validate paths
    if not tasks_dir.exists():
        print(f'ERROR: tasks-dir not found: {tasks_dir}', file=sys.stderr)
        sys.exit(1)
    if not gemini_cli.exists():
        print(f'ERROR: gemini-cli path not found: {gemini_cli}', file=sys.stderr)
        sys.exit(1)
    if not repo_base.exists():
        print(f'ERROR: repo-base not found: {repo_base}', file=sys.stderr)
        sys.exit(1)

    gen = EvalGenerator(gemini_cli_path=gemini_cli, repo_base=repo_base)

    if args.dry_run:
        print('[DRY RUN] No files will be written.\n')

    generated = gen.generate_all(
        task_dir=tasks_dir,
        dry_run=args.dry_run,
        task_ids=args.tasks,
    )

    if generated and not args.dry_run:
        print(f'\nNext step — type-check generated evals:')
        print(f'  cd {gemini_cli} && npx tsc --noEmit evals/longcontext/*.eval.ts')
        print(f'\nRun a single eval:')
        print(f'  cd {gemini_cli} && RUN_EVALS=1 npx vitest run evals/longcontext/flask-001.eval.ts')


if __name__ == '__main__':
    main()