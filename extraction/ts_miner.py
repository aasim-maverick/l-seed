"""
extraction/ts_miner.py

TypeScript-specific mining strategy.

WHY THIS EXISTS
---------------
TypeScript's compiler (src/compiler/) uses a shared namespace pattern.
Files like checker.ts, binder.ts, and types.ts don't import each other
directly — they all import via `_namespaces/ts.ts`, a barrel that
re-exports everything. The result is an import graph with a star topology
(everything → barrel → nothing), which makes import-graph-based CDM
useless: every commit either surfaces only `_namespaces/ts.ts` (noise)
or nothing at all.

STRATEGY
--------
Instead of following import edges, we build a symbol-to-definition map:
    { "CheckFlags": "src/compiler/types.ts",
      "createChecker": "src/compiler/checker.ts", ... }

For each candidate commit we:
  1. Extract all capitalised identifiers from the diff's added/removed lines.
  2. Look up which compiler file defines each identifier.
  3. Required context = definition files that are NOT in the changed set.
  4. Score = number of unique external definition files / total referenced.

This surfaces commits where the changed files reference types, functions,
or constants defined in sibling compiler files — exactly the cross-file
reasoning our benchmark is designed to test.

OUTPUT FORMAT
-------------
Same schema as git_miner.py candidates so the rest of the pipeline is
unchanged.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Optional

                                                                                

REPO_BASE = Path("data/repos")
OUTPUT_DIR = Path("data/tasks/raw")

TS_REPO = REPO_BASE / "typescript"
COMPILER_DIR = "src/compiler"                                          
N_COMMITS = 400
MIN_EXTERNAL_FILES = 1                                                       
MIN_SCORE = 0.08                                                             
OUTPUT_FILE = OUTPUT_DIR / "typescript_candidates.json"

                                                                  
BARREL_PATTERNS = (
    "_namespaces",
    "index.ts",
    "corePublic.ts",
    "utilitiesPublic.ts",
    "visitorPublic.ts",
    "watchPublic.ts",
    "tsbuildPublic.ts",
)

                                                              
_NOISE = frozenset({
    "undefined", "never", "void", "null", "true", "false",
    "string", "number", "boolean", "object", "symbol", "bigint",
    "any", "unknown", "this", "super",
                                     
    "Node", "Type", "Symbol", "Signature", "Declaration",
    "SourceFile", "Program", "TypeChecker", "EmitFlags",
    "SyntaxKind", "TypeFlags", "SymbolFlags", "NodeFlags",
    "ModifierFlags", "TransformFlags", "CheckFlags",
    "InternalSymbolName", "Extension", "ScriptTarget",
    "ModuleKind", "LanguageVariant",
})

                                                  
MIN_SYM_LEN = 5


                                                                                

def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git"] + args, cwd=str(cwd), capture_output=True, text=True
    ).stdout


def _commits(n: int) -> list[tuple[str, str]]:
    """Return (sha, message) pairs for the last n commits."""
    out = _git(["log", "--format=%H|%s", f"-{n}"], TS_REPO)
    result = []
    for line in out.strip().split("\n"):
        if "|" not in line:
            continue
        sha, msg = line.split("|", 1)
        result.append((sha.strip(), msg.strip()))
    return result


def _changed_compiler_files(sha: str) -> list[str]:
    """Return .ts source files changed in sha that live in src/compiler."""
    out = _git(["diff", "--name-only", f"{sha}^", sha], TS_REPO)
    return [
        f.strip() for f in out.strip().split("\n")
        if f.strip().startswith(COMPILER_DIR)
        and f.strip().endswith(".ts")
        and not f.strip().endswith(".d.ts")
        and "_namespaces" not in f
        and "testRunner" not in f
        and "unittests" not in f
    ]


def _diff(sha: str) -> str:
    return _git(["diff", f"{sha}^", sha], TS_REPO)


                                                                                

                                                                   
                                                                 
_EXPORT_DECL_RE = re.compile(
    r"^export\s+(?:(?:abstract|declare|const|readonly|default)\s+)*"
    r"(?:function\*?\s+|class\s+|const\s+|let\s+|var\s+|"
    r"type\s+|interface\s+|enum\s+|namespace\s+)"
    r"([A-Za-z_$][A-Za-z0-9_$]*)",
    re.MULTILINE,
)

                                                           
_REEXPORT_RE = re.compile(r"export\s*\{([^}]+)\}", re.DOTALL)


def _build_symbol_map(repo: Path) -> dict[str, str]:
    """
    Walk every .ts file in src/compiler (excluding barrels/tests) and
    build { symbol_name: relative_filepath } for every exported declaration.

    When a symbol is defined in multiple files (e.g. overloads or merges),
    we keep the first definition found.  This is good enough for our purposes.
    """
    sym_map: dict[str, str] = {}
    compiler = repo / COMPILER_DIR

    for ts_file in sorted(compiler.rglob("*.ts")):
        if ts_file.name.endswith(".d.ts"):
            continue
        rel = str(ts_file.relative_to(repo)).replace("\\", "/")
        if any(b in rel for b in BARREL_PATTERNS):
            continue
        if "testRunner" in rel or "unittests" in rel or "harness" in rel:
            continue

        try:
            content = ts_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

                                    
        for m in _EXPORT_DECL_RE.finditer(content):
            name = m.group(1)
            if name and len(name) >= MIN_SYM_LEN and name not in sym_map:
                sym_map[name] = rel

                                              
        for m in _REEXPORT_RE.finditer(content):
            for part in m.group(1).split(","):
                tok = part.strip().split(" as ")[0].strip()
                if tok and len(tok) >= MIN_SYM_LEN and tok not in sym_map:
                    sym_map[tok] = rel

    return sym_map


                                                                                

_IDENT_RE = re.compile(r"\b([A-Za-z_$][A-Za-z0-9_$]+)\b")


def _symbols_in_diff(diff_text: str) -> set[str]:
    """
    Extract identifiers from added (+) and removed (-) lines.
    Only uppercase-starting identifiers are meaningful for TS exports.
    """
    symbols: set[str] = set()
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if not (line.startswith("+") or line.startswith("-")):
            continue
        for tok in _IDENT_RE.findall(line[1:]):
            if (
                len(tok) >= MIN_SYM_LEN
                and tok[0].isupper()
                and tok not in _NOISE
            ):
                symbols.add(tok)
    return symbols


                                                                                

def _is_barrel(filepath: str) -> bool:
    return any(b in filepath for b in BARREL_PATTERNS)


def _analyze(
    sha: str,
    changed_files: list[str],
    diff_text: str,
    sym_map: dict[str, str],
) -> Optional[dict]:
    """
    Compute required context for a commit using the symbol definition map.
    Returns a candidate dict or None if the commit doesn't qualify.
    """
    diff_symbols = _symbols_in_diff(diff_text)
    if not diff_symbols:
        return None

    changed_set = set(changed_files)

                                                           
    definition_hits: dict[str, list[str]] = {}                                      
    for sym in diff_symbols:
        defn_file = sym_map.get(sym)
        if defn_file and defn_file not in changed_set and not _is_barrel(defn_file):
            definition_hits.setdefault(defn_file, []).append(sym)

    if not definition_hits:
        return None

    required_files = sorted(definition_hits.keys())

                                                                          
    external_sym_count = sum(len(v) for v in definition_hits.values())
    score = external_sym_count / max(1, len(diff_symbols))

    if len(required_files) < MIN_EXTERNAL_FILES or score < MIN_SCORE:
        return None

                                    
    details = [
        {
            "file": f,
            "symbols_used": syms,
            "centrality": 0.0,                        
            "hop_distance": 1,
            "is_constraint_bearing": True,                                           
            "constraint_type": "definition",
        }
        for f, syms in sorted(definition_hits.items())
    ]

    return {
        "repo_id": "typescript",
        "language": "typescript",
        "sha": sha,
        "changed_files": changed_files,
        "cdm": {
            "changed_files": changed_files,
            "required_context_files": required_files,
            "context_distance_hops": 1,
            "irreducibility_score": round(min(1.0, score * 3), 4),
            "constraint_bearing_files": required_files,
            "constraint_types_found": ["definition"],
            "required_context_details": details,
        },
        "diff_preview": diff_text[:1200],
    }


                                                                                

def mine_typescript() -> list[dict]:
    print("\n" + "=" * 60)
    print("Mining typescript (symbol-definition strategy)")
    print("=" * 60)

    if not TS_REPO.exists():
        print(f"  SKIP — repo not found at {TS_REPO}")
        return []

    print("  Building symbol definition map …", flush=True)
    sym_map = _build_symbol_map(TS_REPO)
    print(f"  Symbol map: {len(sym_map)} exported symbols")

    commits = _commits(N_COMMITS)
    print(f"  Scanning {len(commits)} commits …")

    candidates: list[dict] = []
    total_checked = 0

    for sha, message in commits:
        changed = _changed_compiler_files(sha)
        if not changed:
            continue
        total_checked += 1

        diff_text = _diff(sha)
        if not diff_text:
            continue

        result = _analyze(sha, changed, diff_text, sym_map)
        if result is None:
            continue

        result["message"] = message
        candidates.append(result)

        cdm = result["cdm"]
        print(
            f"  FOUND [{sha[:8]}] {message[:55]}\n"
            f"    changed : {changed}\n"
            f"    required: {cdm['required_context_files']}\n"
            f"    irr={cdm['irreducibility_score']:.2f}  "
            f"external_syms={sum(len(d['symbols_used']) for d in cdm['required_context_details'])}"
        )

    print(f"\n  Checked {total_checked} compiler commits → {len(candidates)} candidates")
    return candidates


def run() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    candidates = mine_typescript()

                                                                       
    candidates.sort(key=lambda c: c["cdm"]["irreducibility_score"], reverse=True)

    OUTPUT_FILE.write_text(json.dumps(candidates, indent=2))
    print(f"  Saved → {OUTPUT_FILE}")

                          
    print("\nTop candidates by irreducibility score:")
    for c in candidates[:10]:
        print(
            f"  [{c['sha'][:8]}] irr={c['cdm']['irreducibility_score']:.2f}  "
            f"req={len(c['cdm']['required_context_files'])} files  "
            f"{c['message'][:55]}"
        )


if __name__ == "__main__":
    run()