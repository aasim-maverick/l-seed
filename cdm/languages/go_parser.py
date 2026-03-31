"""
cdm/languages/go_parser.py  (Day 2 — fixed)

Fixes:
1. _pkg_to_files key is full module path (e.g. github.com/gin-gonic/gin/render).
   Old code stripped the prefix → 0 edges. Now uses imp_pkg directly.
2. Added get_interface_names() so stage-3 can detect Go interface constraints.
3. get_exported_symbols() now also includes interface and struct type names
   (not just function/var/const names) at min_len=3 to capture short Go names.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import networkx as nx


                                                                                

_IMPORT_BLOCK_RE = re.compile(r'import\s*\(\s*(.*?)\s*\)', re.DOTALL)
_SINGLE_IMPORT_RE = re.compile(
    r'^import\s+(?:\w+\s+)?["\']([^"\']+)["\']', re.MULTILINE
)

                                                                           
_FUNC_RE = re.compile(r'^func\s+([A-Z][A-Za-z0-9_]*)\s*\(', re.MULTILINE)
_TYPE_RE = re.compile(r'^type\s+([A-Z][A-Za-z0-9_]*)\s+\w', re.MULTILINE)
_VAR_CONST_RE = re.compile(r'^(?:var|const)\s+([A-Z][A-Za-z0-9_]*)\b', re.MULTILINE)

                                                                  
_METHOD_RECEIVER_RE = re.compile(
    r'^func\s+\(\s*\w+\s+\*?([A-Z][A-Za-z0-9_]*)\s*\)', re.MULTILINE
)

                       
_INTERFACE_RE = re.compile(
    r'^type\s+([A-Z][A-Za-z0-9_]*)\s+interface\s*\{', re.MULTILINE
)

                    
_STRUCT_RE = re.compile(
    r'^type\s+([A-Z][A-Za-z0-9_]*)\s+struct\s*\{', re.MULTILINE
)

                                                                                
_COMPLIANCE_RE = re.compile(
    r'var\s+_\s+([A-Z][A-Za-z0-9_]*)\s+=', re.MULTILINE
)


class GoImportParser:
    """
    Builds a directed import graph for a Go repository.
    Nodes: file paths relative to repo root.
    Edges: file A imports a package that file B belongs to.
    """

    _SKIP_DIRS = {".git", "vendor", "testdata", "node_modules"}

    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root).resolve()
        self._module_path: str = self._detect_module_path()
        self._pkg_to_files: dict[str, list[str]] = {}
        self._file_to_pkg: dict[str, str] = {}

                                                                                

    def build_import_graph(self) -> nx.DiGraph:
        G = nx.DiGraph()
        go_files = self._collect_files()

                                                                                
        for f in go_files:
            rel = str(f.relative_to(self.repo_root)).replace("\\", "/")
            pkg = self._file_package(f)
            self._file_to_pkg[rel] = pkg
            self._pkg_to_files.setdefault(pkg, []).append(rel)
            G.add_node(rel)

                           
        for f in go_files:
            src = str(f.relative_to(self.repo_root)).replace("\\", "/")
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
                for imp_pkg in self._extract_imports(content):
                                             
                    if not imp_pkg.startswith(self._module_path):
                        continue
                                                                               
                    target_files = self._pkg_to_files.get(imp_pkg, [])
                    for target in target_files:
                        if target != src and not G.has_edge(src, target):
                            G.add_edge(src, target, symbols=[])
            except Exception:
                pass

        return G

    def get_exported_symbols(self, filepath: str) -> set[str]:
        """
        Return all exported (uppercase) identifiers in a Go file.
        Includes function names, type names, var/const names, receiver types.
        Uses min_len=3 (not 4) because Go uses short exported names like 'Gin'.
        """
        full = self.repo_root / filepath
        if not full.exists():
            return set()
        try:
            content = full.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return set()

        symbols: set[str] = set()
        min_len = 3                                                

        for pattern in (_FUNC_RE, _TYPE_RE, _VAR_CONST_RE,
                        _METHOD_RECEIVER_RE, _INTERFACE_RE, _STRUCT_RE):
            for m in pattern.finditer(content):
                name = m.group(1)
                if len(name) >= min_len:
                    symbols.add(name)

        return symbols

    def get_interface_names(self, filepath: str) -> set[str]:
        """Return names of all exported Go interfaces defined in this file."""
        full = self.repo_root / filepath
        if not full.exists():
            return set()
        try:
            content = full.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return set()
        return {m.group(1) for m in _INTERFACE_RE.finditer(content)}

    def get_struct_names(self, filepath: str) -> set[str]:
        """Return names of all exported Go structs defined in this file."""
        full = self.repo_root / filepath
        if not full.exists():
            return set()
        try:
            content = full.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return set()
        return {m.group(1) for m in _STRUCT_RE.finditer(content)}

    def has_interface_compliance_checks(self, filepath: str) -> bool:
        """Return True if file has `var _ InterfaceName = ...` patterns."""
        full = self.repo_root / filepath
        if not full.exists():
            return False
        try:
            content = full.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return False
        return bool(_COMPLIANCE_RE.search(content))

                                                                                

    def _detect_module_path(self) -> str:
        go_mod = self.repo_root / "go.mod"
        if go_mod.exists():
            for line in go_mod.read_text(errors="ignore").splitlines():
                line = line.strip()
                if line.startswith("module "):
                    return line.split()[1].strip()
        return ""

    def _collect_files(self) -> list[Path]:
        files = []
        for f in self.repo_root.rglob("*.go"):
            if any(skip in f.parts for skip in self._SKIP_DIRS):
                continue
            if f.name.endswith("_test.go"):
                continue
            files.append(f)
        return files

    def _file_package(self, filepath: Path) -> str:
        rel_dir = str(filepath.parent.relative_to(self.repo_root)).replace("\\", "/")
        if rel_dir == ".":
            return self._module_path
        return f"{self._module_path}/{rel_dir}" if self._module_path else rel_dir

    def _extract_imports(self, content: str) -> list[str]:
        pkgs: list[str] = []
                               
        for block_m in _IMPORT_BLOCK_RE.finditer(content):
            for line in block_m.group(1).splitlines():
                line = line.strip()
                m = re.search(r'["\']([^"\']+)["\']', line)
                if m:
                    pkgs.append(m.group(1))
                     
        for m in _SINGLE_IMPORT_RE.finditer(content):
            pkgs.append(m.group(1))
        return pkgs