"""
cdm/languages/python_parser.py  (Day 2 — fixed)

Key fix: added ast.AnnAssign handling so annotated module-level assignments
like `session: "SessionMixin" = LocalProxy(...)` are captured as exported
symbols. Previously these were silently dropped, causing globals.py to
export nothing and breaking all downstream CDM stages for Flask.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Optional

import networkx as nx


class PythonImportParser:
    """
    Builds a directed import graph for a Python repository.
    Edge (A -> B) means file A imports something from file B.
    """

    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root).resolve()

    def build_import_graph(self) -> nx.DiGraph:
        G = nx.DiGraph()
        py_files = [
            f for f in self.repo_root.rglob("*.py")
            if "__pycache__" not in str(f)
            and not f.name.startswith("test_")
            and "/tests/" not in str(f).replace("\\", "/")
        ]

        for f in py_files:
            rel = str(f.relative_to(self.repo_root)).replace("\\", "/")
            G.add_node(rel)

        for f in py_files:
            src = str(f.relative_to(self.repo_root)).replace("\\", "/")
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module:
                        names = [alias.name for alias in node.names]
                        target = self._resolve_module(node.module, src, node.level)
                        if target and G.has_node(target):
                            if G.has_edge(src, target):
                                G[src][target]["symbols"].extend(names)
                            else:
                                G.add_edge(src, target, symbols=names)
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            target = self._resolve_module(alias.name, src, 0)
                            if target and G.has_node(target):
                                if not G.has_edge(src, target):
                                    G.add_edge(src, target, symbols=[alias.name])
            except SyntaxError:
                pass
            except Exception:
                pass

        return G

    def _resolve_module(
        self, module_name: str, importing_file: str, level: int = 0
    ) -> Optional[str]:
        parts = module_name.split(".")

        if level > 0:
            base = Path(importing_file).parent
            for _ in range(level - 1):
                base = base.parent
            candidates = [
                str(base / "/".join(parts)) + ".py",
                str(base / "/".join(parts) / "__init__.py"),
            ]
        else:
            candidates = [
                "/".join(parts) + ".py",
                "/".join(parts) + "/__init__.py",
            ]

        for candidate in candidates:
            candidate = candidate.replace("\\", "/")
            if (self.repo_root / candidate).exists():
                return candidate
        return None

    def get_exported_symbols(self, filepath: str) -> set[str]:
        """
        Extract all meaningful top-level names defined in a file.

        Handles:
          - ast.FunctionDef / AsyncFunctionDef / ClassDef
          - ast.Assign  (x = ...)
          - ast.AnnAssign  (x: Type = ...)   ← FIX: was missing previously

        Filters out dunder names and very short names.
        """
        full_path = self.repo_root / filepath
        if not full_path.exists():
            return set()

        try:
            tree = ast.parse(
                full_path.read_text(encoding="utf-8", errors="ignore")
            )
        except SyntaxError:
            return set()

        symbols: set[str] = set()

                                                          
        for node in ast.iter_child_nodes(tree):
                                   
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.name.startswith("__"):
                    symbols.add(node.name)

                                            
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        name = target.id
                        if not name.startswith("__") and len(name) >= 2:
                            symbols.add(name)

                                                               
                                                    
                                                         
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name):
                    name = node.target.id
                    if not name.startswith("__") and len(name) >= 2:
                        symbols.add(name)

        return symbols