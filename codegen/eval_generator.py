"""
codegen/eval_generator.py

Transforms validated task JSON records into runnable Gemini CLI eval files.

File loading strategy
---------------------
Changed files    : loaded from parent commit (pre-fix state the agent sees)
Required context : loaded from the commit itself (unchanged by the diff)

Truncation policy
-----------------
Required context files: NEVER aggressively truncated.
  - If file fits in MAX_REQUIRED_CHARS: include in full.
  - If oversized: use symbol-guided extraction — find regions containing
    the symbols listed in assert_design.constraint_symbols, include ±CONTEXT_LINES
    of context around each hit, separate skipped regions with a comment.

Changed files: truncated conservatively (include full content up to MAX_CHANGED_CHARS).
  Large changed files are uncommon for the kinds of commits we selected.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined

# ── Constants ─────────────────────────────────────────────────────────────────

# Required context files — these are semantically critical.
# Prefer including everything; only extract when truly oversized.
MAX_REQUIRED_CHARS = 60_000   # ~15k tokens — generous intentionally

# Changed files shown to agent as pre-fix baseline.
MAX_CHANGED_CHARS = 16_000    # ~4k tokens

# Lines of surrounding context to include around each symbol hit.
CONTEXT_LINES = 50


# ── Git helpers ───────────────────────────────────────────────────────────────

def _git_show(repo_path: Path, git_ref: str, filepath: str) -> str:
    """Return file content at git_ref:filepath, or '' on failure."""
    result = subprocess.run(
        ['git', 'show', f'{git_ref}:{filepath}'],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    return result.stdout if result.returncode == 0 else ''


# ── Smart truncation ─────────────────────────────────────────────────────────

def _symbol_guided_extract(content: str, symbols: list[str], max_chars: int, lang: str) -> str:
    """
    Extract sections of `content` containing any of `symbols`.
    Returns at most max_chars characters, with gap markers between sections.
    """
    comment_prefix = '//' if lang in ('typescript', 'go') else '#'
    lines = content.split('\n')
    relevant: set[int] = set()

    for sym in symbols:
        for i, line in enumerate(lines):
            if sym in line:
                lo = max(0, i - CONTEXT_LINES)
                hi = min(len(lines), i + CONTEXT_LINES + 1)
                relevant.update(range(lo, hi))

    if not relevant:
        # Symbols not found — include file header (most definitions are near top)
        head = content[:max_chars]
        last_nl = head.rfind('\n')
        head = head[:last_nl] if last_nl > 0 else head
        return head + f'\n{comment_prefix} ... [remaining file omitted — use read_file tool for full content]'

    sorted_idx = sorted(relevant)
    sections: list[tuple[int, int]] = []
    seg_start = sorted_idx[0]
    prev = sorted_idx[0]

    for li in sorted_idx[1:]:
        if li - prev > 3:
            sections.append((seg_start, prev))
            seg_start = li
        prev = li
    sections.append((seg_start, prev))

    parts: list[str] = []
    if sections[0][0] > 0:
        parts.append(
            f'{comment_prefix} ... [lines 1–{sections[0][0]} omitted — not directly relevant]'
        )

    for s_idx, (lo, hi) in enumerate(sections):
        parts.append('\n'.join(lines[lo : hi + 1]))
        if s_idx < len(sections) - 1:
            gap_start = hi + 1
            gap_end = sections[s_idx + 1][0] - 1
            if gap_end >= gap_start:
                parts.append(
                    f'\n{comment_prefix} ... [lines {gap_start}–{gap_end} omitted]\n'
                )

    if sections[-1][1] < len(lines) - 1:
        parts.append(
            f'{comment_prefix} ... [lines {sections[-1][1]+2}–{len(lines)} omitted'
            f' — use read_file tool for full content]'
        )

    result = '\n'.join(parts)
    if len(result) > max_chars:
        result = result[:max_chars]
        last_nl = result.rfind('\n')
        if last_nl > 0:
            result = result[:last_nl]
        result += f'\n{comment_prefix} ... [further truncated — use read_file for full content]'
    return result


def _truncate_changed(content: str, lang: str) -> str:
    """Conservative truncation for changed (pre-fix) files."""
    if len(content) <= MAX_CHANGED_CHARS:
        return content
    comment_prefix = '//' if lang in ('typescript', 'go') else '#'
    head = content[:MAX_CHANGED_CHARS]
    last_nl = head.rfind('\n')
    if last_nl > 0:
        head = head[:last_nl]
    return head + f'\n{comment_prefix} ... [file truncated — use read_file tool for complete content]'


# ── File content loader ───────────────────────────────────────────────────────

def load_files_for_task(task: dict, repo_base: Path) -> dict[str, str]:
    """
    Load all file content needed for this task and return a {path: content} dict.

    - Changed files  → pre-fix state (parent commit)
    - Required files → current state at commit (they are not in the diff)
    """
    repo_id: str = task['repo_id']
    commit_sha: str = task['commit_sha']
    language: str = task.get('language', 'python')
    repo_path = repo_base / repo_id

    # Gather symbols to guide smart extraction of required context files.
    # Pull from assert_design.constraint_symbols as a reliable source.
    guide_symbols: list[str] = task.get('assert_design', {}).get('constraint_symbols', [])

    # Also gather from required_context_details if present (CDM output).
    details: list[dict] = task.get('required_context_details', [])
    symbol_map: dict[str, list[str]] = {}
    for d in details:
        symbol_map.setdefault(d['file'], []).extend(d.get('symbols_used', []))

    required_set = set(task.get('required_context_files', []))
    changed_files: list[str] = task.get('changed_files', [])

    files: dict[str, str] = {}

    # ── Changed files (pre-fix) ───────────────────────────────────────────────
    parent_ref = f'{commit_sha}^'
    for filepath in changed_files:
        content = _git_show(repo_path, parent_ref, filepath)
        if not content:
            # New file added in the commit — show empty or note
            content = _git_show(repo_path, commit_sha, filepath)
        if not content:
            content = f'# File not found at parent commit: {filepath}'

        files[filepath] = _truncate_changed(content, language)

    # ── Required context files ────────────────────────────────────────────────
    for filepath in task.get('required_context_files', []):
        if filepath in files:
            # File also appears in changed_files — already loaded above.
            # Override with the version at the actual commit (not parent)
            # because required context reflects the canonical state.
            pass

        content = _git_show(repo_path, commit_sha, filepath)
        if not content:
            # Fallback: try parent (edge case)
            content = _git_show(repo_path, parent_ref, filepath)
        if not content:
            content = f'# Required context file not found: {filepath}'
            files[filepath] = content
            continue

        # Gather symbols for this file
        file_symbols = list({*guide_symbols, *symbol_map.get(filepath, [])})

        if len(content) <= MAX_REQUIRED_CHARS:
            files[filepath] = content  # Include in full — this is the ideal path
        else:
            print(
                f'  [WARN] {filepath} ({len(content):,} chars) exceeds limit — '
                f'using symbol-guided extraction with {len(file_symbols)} symbols',
                file=sys.stderr,
            )
            files[filepath] = _symbol_guided_extract(content, file_symbols, MAX_REQUIRED_CHARS, language)

    return files


# ── Template renderer ─────────────────────────────────────────────────────────

class EvalGenerator:

    def __init__(self, gemini_cli_path: str | Path, repo_base: str | Path):
        self.gemini_cli_path = Path(gemini_cli_path)
        self.repo_base = Path(repo_base)
        self.output_dir = self.gemini_cli_path / 'evals' / 'longcontext'

        template_dir = Path(__file__).parent / 'templates'
        env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )
        # tojson filter: use json.dumps so the output is valid TypeScript
        env.filters['tojson'] = json.dumps
        self.template = env.get_template('eval_template.ts.jinja2')

    def generate(self, task_json_path: str | Path, dry_run: bool = False) -> Optional[Path]:
        task_json_path = Path(task_json_path)
        with open(task_json_path, encoding='utf-8') as f:
            task = json.load(f)

        task_id: str = task['task_id']
        print(f'\nGenerating {task_id} …')

        # Load file contents from the repository
        files = load_files_for_task(task, self.repo_base)
        task['files'] = files

        # Render the Jinja2 template
        try:
            rendered = self.template.render(task=task)
        except Exception as exc:
            print(f'  [ERROR] Template render failed for {task_id}: {exc}', file=sys.stderr)
            raise

        output_path = self.output_dir / f'{task_id}.eval.ts'

        if dry_run:
            print(f'  DRY RUN — would write to {output_path}')
            print(f'  Total file content: {sum(len(v) for v in files.values()):,} chars')
            for fp, content in files.items():
                truncated_note = '(truncated)' if '... [' in content else '(full)'
                print(f'    {fp}: {len(content):,} chars {truncated_note}')
            return None

        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding='utf-8')
        print(f'  Written → {output_path}')

        # Print file size summary
        total = sum(len(v) for v in files.values())
        print(f'  Files included: {len(files)}, total content: {total:,} chars')
        for fp, content in files.items():
            is_req = fp in set(task.get('required_context_files', []))
            truncated = '(truncated)' if '... [' in content else '(full)'
            label = 'REQUIRED' if is_req else 'changed'
            print(f'    [{label}] {fp}: {len(content):,} chars {truncated}')

        return output_path

    def generate_all(
        self,
        task_dir: str | Path,
        dry_run: bool = False,
        task_ids: Optional[list[str]] = None,
    ) -> list[Path]:
        task_dir = Path(task_dir)
        task_files = sorted(task_dir.glob('*.json'))

        if task_ids:
            task_files = [f for f in task_files if f.stem in task_ids]

        if not task_files:
            print(f'No task JSON files found in {task_dir}', file=sys.stderr)
            return []

        generated: list[Path] = []
        errors: list[str] = []

        for task_file in task_files:
            try:
                out = self.generate(task_file, dry_run=dry_run)
                if out:
                    generated.append(out)
            except Exception as exc:
                errors.append(f'{task_file.stem}: {exc}')
                print(f'  [ERROR] {task_file.stem}: {exc}', file=sys.stderr)

        print(f'\n{"="*50}')
        print(f'Generated {len(generated)} eval files')
        if errors:
            print(f'Failed    {len(errors)} tasks:')
            for e in errors:
                print(f'  {e}')

        return generated