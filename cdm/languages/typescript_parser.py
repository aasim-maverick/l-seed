"""
cdm/languages/typescript_parser.py  (Day 2 — fixed)

Root cause of 19016 nodes / 1434 edges:
  The TS repo's src/lib/ contains ~15 000 .d.ts type-declaration files.
  These were being included as nodes, inflating the graph and causing
  most real source-to-source edges to be drowned out.

Fix: exclude all .d.ts files. Also accept a subtree parameter so the
miner can restrict file collection to e.g. src/compiler.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import networkx as nx

try:
    from tree_sitter import Language, Parser
    import tree_sitter_typescript as _ts_bindings
    _TS_AVAILABLE = True
except ImportError:
    _TS_AVAILABLE = False

# ── Regexes ───────────────────────────────────────────────────────────────────

# Simple: find every `from '...'` in a file — handles multi-line imports
_FROM_RE = re.compile(r"""from\s+['"]([^'"]+)['"]""")

# Named imports: `import { Foo, Bar as B } from …`
_NAMED_IMPORT_RE = re.compile(r"""import\s+(?:type\s+)?\{([^}]+)\}""", re.DOTALL)

# Exported top-level declarations
_EXPORT_RE = re.compile(
    r"^export\s+(?:default\s+)?(?:abstract\s+)?"
    r"(?:class|function|interface|type|enum|const|let|var)\s+"
    r"([A-Za-z_$][A-Za-z0-9_$]*)",
    re.MULTILINE,
)

# Re-exports: `export { Foo, Bar }`
_REEXPORT_RE = re.compile(r"export\s+\{([^}]+)\}", re.DOTALL)


class TypeScriptImportParser:
    """
    Builds a directed import graph for TypeScript source files.

    Key decisions:
    - .d.ts files are excluded (they're type declarations, not source)
    - subtree restricts which files become nodes (default: full repo)
    - Import resolution still searches full repo_root regardless of subtree
    """

    _EXTENSIONS = {".ts", ".tsx"}
    _SKIP_DIRS = {"node_modules", "dist", "build", "out", ".git", "__pycache__"}

    def __init__(self, repo_root: str, subtree: Optional[str] = None):
        self.repo_root = Path(repo_root).resolve()
        self.subtree: Path = (
            self.repo_root / subtree if subtree else self.repo_root
        )
        self._use_treesitter = _TS_AVAILABLE
        if self._use_treesitter:
            try:
                lang = Language(_ts_bindings.language_typescript())
                self._parser = Parser(lang)
            except Exception:
                self._use_treesitter = False

    # ── Public ────────────────────────────────────────────────────────────────

    def build_import_graph(self) -> nx.DiGraph:
        G = nx.DiGraph()
        ts_files = self._collect_files()

        for f in ts_files:
            rel = str(f.relative_to(self.repo_root)).replace("\\", "/")
            G.add_node(rel)

        for f in ts_files:
            src = str(f.relative_to(self.repo_root)).replace("\\", "/")
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
                for imp in self._extract_imports(content):
                    target = self._resolve_import(imp["path"], src)
                    if target and G.has_node(target):
                        if G.has_edge(src, target):
                            G[src][target]["symbols"].extend(imp.get("names", []))
                        else:
                            G.add_edge(src, target, symbols=imp.get("names", []))
            except Exception:
                pass

        return G

    def get_exported_symbols(self, filepath: str) -> set[str]:
        full = self.repo_root / filepath
        if not full.exists():
            return set()
        try:
            content = full.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return set()

        symbols: set[str] = set()
        for m in _EXPORT_RE.finditer(content):
            symbols.add(m.group(1))
        for m in _REEXPORT_RE.finditer(content):
            for part in m.group(1).split(","):
                tok = part.strip().split(" as ")[0].strip()
                if tok and len(tok) >= 2 and not tok.startswith("//"):
                    symbols.add(tok)
        return symbols

    def get_interface_names(self, filepath: str) -> set[str]:
        """Return names of all exported TypeScript interfaces in this file."""
        full = self.repo_root / filepath
        if not full.exists():
            return set()
        try:
            content = full.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return set()
        pattern = re.compile(
            r"^export\s+(?:abstract\s+)?interface\s+([A-Za-z_$][A-Za-z0-9_$]*)",
            re.MULTILINE,
        )
        return {m.group(1) for m in pattern.finditer(content)}

    # ── Private ───────────────────────────────────────────────────────────────

    def _collect_files(self) -> list[Path]:
        files = []
        for ext in self._EXTENSIONS:
            for f in self.subtree.rglob(f"*{ext}"):
                # Skip declaration files — the root cause of 19016 nodes
                if f.name.endswith(".d.ts"):
                    continue
                # Skip blacklisted directories
                if any(skip in f.parts for skip in self._SKIP_DIRS):
                    continue
                # Skip test files
                name = f.name.lower()
                if (
                    "test" in name
                    or "spec" in name
                    or f.parent.name.lower()
                    in {"tests", "__tests__", "test", "testrunner", "unittests", "harness"}
                ):
                    continue
                files.append(f)
        return files

    def _extract_imports(self, content: str) -> list[dict]:
        """Extract all import paths from file content."""
        imports = []
        for m in _FROM_RE.finditer(content):
            path = m.group(1)
            # Try to find named imports near this from clause
            # Search for { ... } before `from`
            before = content[max(0, m.start() - 200) : m.start()]
            names_m = _NAMED_IMPORT_RE.search(before)
            names = []
            if names_m:
                for part in names_m.group(1).split(","):
                    tok = part.strip().split(" as ")[0].strip()
                    if tok and len(tok) >= 2 and not tok.startswith("//"):
                        names.append(tok)
            imports.append({"path": path, "names": names})
        return imports

    def _resolve_import(self, import_path: str, importing_file: str) -> Optional[str]:
        """Resolve a TS import path to a relative filepath within the repo."""
        if not import_path.startswith("."):
            # Handle common path aliases
            if import_path.startswith("@/"):
                import_path = "src/" + import_path[2:]
            else:
                return None  # External dependency

        base = (Path(importing_file).parent / import_path).as_posix()

        # Normalise ../ without touching filesystem
        parts: list[str] = []
        for part in base.split("/"):
            if part == "..":
                if parts:
                    parts.pop()
            elif part != ".":
                parts.append(part)
        normalized = "/".join(parts)

        candidates = [
            normalized + ".ts",
            normalized + ".tsx",
            normalized + "/index.ts",
            normalized + "/index.tsx",
        ]
        for candidate in candidates:
            if (self.repo_root / candidate).exists():
                return candidate
        return None