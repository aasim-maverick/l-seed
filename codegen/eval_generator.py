"""
codegen/eval_generator.py

Transforms validated task JSON records into Gemini CLI eval .ts files.

File loading
------------
- Changed files  : loaded at PARENT commit (pre-fix state the agent sees)
- Required files : loaded at the COMMIT itself (unchanged by diff)

Truncation policy
-----------------
Required context files: included in FULL up to MAX_REQUIRED_CHARS.
  If oversized: symbol-guided extraction — locate regions containing the
  symbols listed in assert_design.constraint_symbols and required_context_details,
  include ±CONTEXT_LINES around each hit, skipped regions get gap markers.

Changed files: up to MAX_CHANGED_CHARS.
  Rare for the commit sizes we select; conservative cap avoids token waste.

The `md5` Jinja filter is registered so BASELINE_HASHES in the template
can be computed from the baseline file content at render time.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined

                                                                                

MAX_REQUIRED_CHARS = 80_000                                           
MAX_CHANGED_CHARS  = 20_000                                
CONTEXT_LINES      = 60                                                          


                                                                                

def _git_show(repo_path: Path, git_ref: str, filepath: str) -> str:
    result = subprocess.run(
        ['git', 'show', f'{git_ref}:{filepath}'],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    return result.stdout if result.returncode == 0 else ''


def _md5(content: str) -> str:
    return hashlib.md5(content.encode('utf-8')).hexdigest()


                                                                               

def _comment(language: str) -> str:
    return '//' if language in ('typescript', 'go') else '#'


def _symbol_guided_extract(
    content: str,
    symbols: list[str],
    max_chars: int,
    language: str,
    filepath: str,
) -> str:
    """Include only regions surrounding matched symbols, with gap markers."""
    cmt = _comment(language)
    lines = content.split('\n')
    relevant: set[int] = set()

    for sym in symbols:
        for i, line in enumerate(lines):
            if sym in line:
                lo = max(0, i - CONTEXT_LINES)
                hi = min(len(lines), i + CONTEXT_LINES + 1)
                relevant.update(range(lo, hi))

    if not relevant:
        print(
            f'  [WARN] No guide symbols found in {filepath} — '
            f'including file header only',
            file=sys.stderr,
        )
        head = content[:max_chars]
        cut = head.rfind('\n')
        return (head[:cut] if cut > 0 else head) + (
            f'\n{cmt} ... [remainder omitted — use read_file for full content]'
        )

    sorted_idx = sorted(relevant)
    sections: list[tuple[int, int]] = []
    seg_start = sorted_idx[0]
    prev = sorted_idx[0]

    for li in sorted_idx[1:]:
        if li - prev > 4:
            sections.append((seg_start, prev))
            seg_start = li
        prev = li
    sections.append((seg_start, prev))

    parts: list[str] = []
    if sections[0][0] > 0:
        parts.append(f'{cmt} [lines 1–{sections[0][0]} omitted]')

    for s_idx, (lo, hi) in enumerate(sections):
        parts.append('\n'.join(lines[lo : hi + 1]))
        if s_idx < len(sections) - 1:
            gap_start = hi + 1
            gap_end = sections[s_idx + 1][0] - 1
            if gap_end >= gap_start:
                parts.append(f'\n{cmt} [lines {gap_start}–{gap_end} omitted]\n')

    last_hi = sections[-1][1]
    if last_hi < len(lines) - 1:
        parts.append(
            f'{cmt} [lines {last_hi + 2}–{len(lines)} omitted'
            f' — use read_file for full content]'
        )

    result = '\n'.join(parts)
    if len(result) > max_chars:
        cut = result[:max_chars].rfind('\n')
        result = result[:cut] if cut > 0 else result[:max_chars]
        result += f'\n{cmt} [further truncated — use read_file for full content]'
    return result


def _truncate_changed(content: str, language: str, filepath: str) -> str:
    if len(content) <= MAX_CHANGED_CHARS:
        return content
    cmt = _comment(language)
    head = content[:MAX_CHANGED_CHARS]
    cut = head.rfind('\n')
    head = head[:cut] if cut > 0 else head
    print(
        f'  [INFO] Truncated changed file {filepath} to {MAX_CHANGED_CHARS:,} chars',
        file=sys.stderr,
    )
    return head + f'\n{cmt} ... [file truncated — use read_file for complete content]'


                                                                                

def load_files_for_task(task: dict, repo_base: Path) -> dict[str, str]:
    repo_id: str = task['repo_id']
    commit_sha: str = task['commit_sha']
    language: str = task.get('language', 'python')
    repo_path = repo_base / repo_id

                                                                        
    guide_symbols: list[str] = list(set(
        task.get('assert_design', {}).get('constraint_symbols', [])
    ))
                                        
    for d in task.get('required_context_details', []):
        guide_symbols.extend(d.get('symbols_used', []))
                                                                
    for t in task.get('constraint_propagation_targets', []):
                                                  
        import re
        ids = re.findall(r'[A-Za-z_][A-Za-z0-9_]{2,}', t.get('pattern', ''))
        guide_symbols.extend(ids)
    guide_symbols = list(set(guide_symbols))

    required_set = set(task.get('required_context_files', []))
    changed_files: list[str] = task.get('changed_files', [])
    parent_ref = f'{commit_sha}^'

    files: dict[str, str] = {}

                                                                                
    for filepath in changed_files:
        content = _git_show(repo_path, parent_ref, filepath)
        if not content:
                                                                     
            content = f'{_comment(language)} New file (did not exist before this commit)'
        files[filepath] = _truncate_changed(content, language, filepath)

                                                                               
    for filepath in task.get('required_context_files', []):
        content = _git_show(repo_path, commit_sha, filepath)
        if not content:
            content = _git_show(repo_path, parent_ref, filepath)
        if not content:
            content = f'{_comment(language)} Required context file not found: {filepath}'
            files[filepath] = content
            print(f'  [WARN] Required context file not found: {filepath}', file=sys.stderr)
            continue

        if len(content) <= MAX_REQUIRED_CHARS:
            files[filepath] = content                      
        else:
            print(
                f'  [INFO] {filepath} ({len(content):,} chars) > {MAX_REQUIRED_CHARS:,} limit'
                f' — applying symbol-guided extraction ({len(guide_symbols)} symbols)',
                file=sys.stderr,
            )
            files[filepath] = _symbol_guided_extract(
                content, guide_symbols, MAX_REQUIRED_CHARS, language, filepath
            )

    return files


                                                                                

def _make_env(template_dir: Path) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    env.filters['tojson'] = json.dumps
    env.filters['md5'] = _md5
    return env


                                                                               

class EvalGenerator:

    def __init__(self, gemini_cli_path: str | Path, repo_base: str | Path):
        self.gemini_cli_path = Path(gemini_cli_path)
        self.repo_base = Path(repo_base)
        self.output_dir = self.gemini_cli_path / 'evals' / 'l-seed'

        template_dir = Path(__file__).parent / 'templates'
        self._env = _make_env(template_dir)
        self._template = self._env.get_template('eval_template.ts.jinja2')

    def generate(self, task_json_path: str | Path, dry_run: bool = False) -> Optional[Path]:
        task_json_path = Path(task_json_path)
        with open(task_json_path, encoding='utf-8') as f:
            task = json.load(f)

        task_id: str = task['task_id']
        print(f'\n{"─"*55}')
        print(f'Generating {task_id}  [{task["difficulty_level"]} | {task["language"]}]')

        files = load_files_for_task(task, self.repo_base)
        task['files'] = files

        try:
            rendered = self._template.render(task=task)
        except Exception as exc:
            print(f'  [ERROR] Template render failed: {exc}', file=sys.stderr)
            raise

        output_path = self.output_dir / f'{task_id}.eval.ts'

        if dry_run:
            print(f'  DRY RUN → {output_path}')
        else:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding='utf-8')
            print(f'  Written  → {output_path}')

                 
        total = sum(len(v) for v in files.values())
        print(f'  Files: {len(files)} included, {total:,} chars total')
        for fp, content in files.items():
            is_req = fp in set(task.get('required_context_files', []))
            trunc = '(truncated)' if '... [' in content else '(full)'
            label = 'REQUIRED' if is_req else 'changed '
            print(f'    [{label}] {fp}: {len(content):,} chars {trunc}')

        return None if dry_run else output_path

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

        for tf in task_files:
            try:
                out = self.generate(tf, dry_run=dry_run)
                if out:
                    generated.append(out)
            except Exception as exc:
                errors.append(f'{tf.stem}: {exc}')

        print(f'\n{"="*55}')
        print(f'Generated : {len(generated)} eval files')
        if errors:
            print(f'Failed    : {len(errors)}')
            for e in errors:
                print(f'  {e}')
        return generated
