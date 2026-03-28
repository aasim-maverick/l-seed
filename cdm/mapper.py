"""
cdm/mapper.py  (Day 2 — fixed)

Fixes:
1. Accepts `subtree` parameter and passes it to TypeScript parser.
2. Stage 3 is now language-aware:
     Python: ABC/Protocol/TypedDict/dataclass/type-alias detection (unchanged)
     Go:     exported interface detection (new)
     TS:     exported interface/abstract class detection (new)
3. Stage 4 is now language-aware:
     Python/TS: symbol-overlap-based fractional score (unchanged)
     Go:        interface-count + centrality + structural score (new)
4. Min symbol length is 3 for Go (Go exports short names: Gin, Ctx, Key).
5. _symbols_from_diff now includes context and removed lines (not just +lines).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import networkx as nx


# ── Noise filter ──────────────────────────────────────────────────────────────

_NOISE_SYMBOLS: frozenset[str] = frozenset({
    "self", "cls", "None", "True", "False", "str", "int", "float",
    "bool", "list", "dict", "set", "tuple", "type", "Any", "Optional",
    "Union", "List", "Dict", "Tuple", "Set", "Type", "Iterator",
    "Generator", "Callable", "Sequence", "Mapping", "Iterable",
    "return", "yield", "raise", "pass", "import", "from", "class",
    "def", "if", "else", "elif", "for", "while", "with", "try",
    "except", "finally", "and", "or", "not", "in", "is", "as",
    "lambda", "async", "await", "super", "object",
    # Go-specific noise
    "nil", "make", "new", "len", "cap", "append", "delete",
    "range", "chan", "func", "interface", "struct", "map",
    "var", "const", "type", "package", "import", "return",
    "defer", "go", "select", "case", "default", "fallthrough",
    "break", "continue", "goto",
})

# Minimum token length — 3 for Go (short exported names), 4 for others
_MIN_LEN_DEFAULT = 4
_MIN_LEN_GO = 3


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class SymbolDependency:
    file: str
    symbols_used: list[str]
    centrality: float
    hop_distance: int
    is_constraint_bearing: bool = False
    constraint_type: str = ""


@dataclass
class ContextDependencyMap:
    changed_files: list[str]
    required_context_files: list[str]
    required_context_details: list[SymbolDependency]
    context_distance_hops: int
    irreducibility_score: float
    symbol_dependencies: list[SymbolDependency]
    constraint_bearing_files: list[str] = field(default_factory=list)
    constraint_types_found: list[str] = field(default_factory=list)

    def is_valid_task(self, min_irr: float = 0.25) -> bool:
        return (
            len(self.required_context_files) > 0
            and self.irreducibility_score >= min_irr
            and self.context_distance_hops >= 1
        )

    def to_dict(self) -> dict:
        return {
            "changed_files": self.changed_files,
            "required_context_files": self.required_context_files,
            "context_distance_hops": self.context_distance_hops,
            "irreducibility_score": round(self.irreducibility_score, 4),
            "constraint_bearing_files": self.constraint_bearing_files,
            "constraint_types_found": self.constraint_types_found,
            "required_context_details": [
                {
                    "file": d.file,
                    "symbols_used": d.symbols_used,
                    "centrality": round(d.centrality, 5),
                    "hop_distance": d.hop_distance,
                    "is_constraint_bearing": d.is_constraint_bearing,
                    "constraint_type": d.constraint_type,
                }
                for d in self.required_context_details
            ],
        }


# ── Diff symbol extraction ────────────────────────────────────────────────────

def _symbols_from_diff(diff_text: str, min_len: int = _MIN_LEN_DEFAULT) -> set[str]:
    """
    Extract identifiers from ALL code lines in a unified diff:
    context lines, added lines (+), and removed lines (-).
    Including removed/context lines captures dependencies in the surrounding
    code that must be understood even when not explicitly rewritten.
    """
    symbols: set[str] = set()
    for line in diff_text.splitlines():
        if (line.startswith("+++") or line.startswith("---")
                or line.startswith("@@") or line.startswith("diff ")
                or line.startswith("index ") or line.startswith("new file")
                or line.startswith("old file")):
            continue
        code = line[1:] if (line.startswith("+") or line.startswith("-")
                             or line.startswith(" ")) else None
        if code is None:
            continue
        for tok in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]+)\b", code):
            if len(tok) >= min_len and tok not in _NOISE_SYMBOLS:
                symbols.add(tok)
    return symbols


def _annotation_symbols_from_diff(diff_text: str) -> set[str]:
    """Symbols in type-annotation positions of added lines — higher confidence."""
    symbols: set[str] = set()
    for line in diff_text.splitlines():
        if line.startswith("+++") or not line.startswith("+"):
            continue
        code = line[1:]
        for m in re.finditer(r"->\s*([A-Za-z_][A-Za-z0-9_\[\], |]*?)(?:\s*:|\s*$)", code):
            for tok in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]+)\b", m.group(1)):
                if len(tok) >= 4 and tok not in _NOISE_SYMBOLS:
                    symbols.add(tok)
        for m in re.finditer(r":\s*([A-Za-z_][A-Za-z0-9_\[\], |]*?)(?:\s*[,)=])", code):
            for tok in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]+)\b", m.group(1)):
                if len(tok) >= 4 and tok not in _NOISE_SYMBOLS:
                    symbols.add(tok)
    return symbols


# ── Python constraint classifier ──────────────────────────────────────────────

import ast as _ast

_ABC_BASES = {"ABC", "ABCMeta", "Protocol"}
_TYPED_DICT_BASES = {"TypedDict"}
_PROTOCOL_BASES = {"Protocol"}
_DATACLASS_DECORATORS = {"dataclass", "attrs", "attr.s"}


def _classify_constraint_python(filepath: str, repo_root: Path) -> tuple[bool, str]:
    full = repo_root / filepath
    if not full.exists():
        return False, ""
    try:
        tree = _ast.parse(full.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return False, ""

    for node in _ast.walk(tree):
        if isinstance(node, _ast.ClassDef):
            base_names = {
                b.id if isinstance(b, _ast.Name) else
                b.attr if isinstance(b, _ast.Attribute) else ""
                for b in node.bases
            }
            if base_names & _ABC_BASES:
                return True, "abc"
            if base_names & _TYPED_DICT_BASES:
                return True, "typeddict"
            if base_names & _PROTOCOL_BASES:
                return True, "protocol"
            for d in node.decorator_list:
                dname = (d.id if isinstance(d, _ast.Name) else
                         d.attr if isinstance(d, _ast.Attribute) else
                         d.func.id if isinstance(d, _ast.Call) and isinstance(d.func, _ast.Name) else "")
                if dname in _DATACLASS_DECORATORS:
                    return True, "dataclass"

        if isinstance(node, _ast.Assign):
            for t in node.targets:
                if isinstance(t, _ast.Name) and t.id[0:1].isupper():
                    rhs = _ast.unparse(node.value) if hasattr(_ast, "unparse") else ""
                    if any(kw in rhs for kw in ("Union", "Optional", "Literal", "TypeVar")):
                        return True, "type_alias"

    return False, ""


# ── Go constraint classifier ──────────────────────────────────────────────────

_GO_INTERFACE_RE = re.compile(
    r'^type\s+([A-Z][A-Za-z0-9_]*)\s+interface\s*\{', re.MULTILINE
)


def _classify_constraint_go(filepath: str, repo_root: Path, parser) -> tuple[bool, str]:
    """
    A Go file is constraint-bearing if it defines exported interfaces.
    Go's type system uses structural (duck-type) interfaces — any file
    that defines an interface type is an implicit constraint on callers.
    """
    interfaces = parser.get_interface_names(filepath)
    if interfaces:
        return True, "interface"
    # Struct types used as function parameter/return types are also constraints
    structs = parser.get_struct_names(filepath)
    if structs:
        return True, "struct"
    return False, ""


# ── TS constraint classifier ──────────────────────────────────────────────────

def _classify_constraint_ts(filepath: str, repo_root: Path, parser) -> tuple[bool, str]:
    interfaces = parser.get_interface_names(filepath)
    if interfaces:
        return True, "interface"
    fname = Path(filepath).name.lower()
    if any(kw in fname for kw in ("type", "types", "interface", "model", "schema")):
        return True, "type_file"
    return False, ""


# ── Lifecycle heuristic (language-agnostic) ───────────────────────────────────

_LIFECYCLE_RE = re.compile(
    r"\b(open|close|push|pop|init|setup|teardown|create|destroy|"
    r"start|stop|begin|end|enter|exit|acquire|release)\b",
    re.IGNORECASE,
)


# ── Main mapper ───────────────────────────────────────────────────────────────

class ContextDependencyMapper:

    def __init__(
        self,
        repo_path: str,
        language: str = "python",
        subtree: Optional[str] = None,
    ):
        self.repo_path = Path(repo_path).resolve()
        self.language = language.lower()
        self.subtree = subtree
        self._min_len = _MIN_LEN_GO if self.language == "go" else _MIN_LEN_DEFAULT

        self.G: nx.DiGraph = self._build_graph()
        self.centrality: dict[str, float] = nx.betweenness_centrality(self.G)
        self._symbol_cache: dict[str, set[str]] = {}

    # ── Graph ─────────────────────────────────────────────────────────────────

    def _build_graph(self) -> nx.DiGraph:
        if self.language == "python":
            from cdm.languages.python_parser import PythonImportParser
            self._parser = PythonImportParser(str(self.repo_path))
        elif self.language == "typescript":
            from cdm.languages.typescript_parser import TypeScriptImportParser
            self._parser = TypeScriptImportParser(
                str(self.repo_path), subtree=self.subtree
            )
        elif self.language == "go":
            from cdm.languages.go_parser import GoImportParser
            self._parser = GoImportParser(str(self.repo_path))
        else:
            raise NotImplementedError(f"Unsupported language: {self.language}")
        return self._parser.build_import_graph()

    def _exported_symbols(self, filepath: str) -> set[str]:
        if filepath not in self._symbol_cache:
            self._symbol_cache[filepath] = self._parser.get_exported_symbols(filepath)
        return self._symbol_cache[filepath]

    def _min_hop_distance(self, changed_files: list[str], target: str) -> int:
        min_dist = float("inf")
        for cf in changed_files:
            if not self.G.has_node(cf) or not self.G.has_node(target):
                continue
            try:
                d = nx.shortest_path_length(self.G, cf, target)
                if d < min_dist:
                    min_dist = d
            except nx.NetworkXNoPath:
                pass
        return int(min_dist) if min_dist != float("inf") else 1

    # ── Stage 1 ───────────────────────────────────────────────────────────────

    def _stage1_syntactic_deps(self, changed_files: list[str]) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for cf in changed_files:
            if not self.G.has_node(cf):
                continue
            deps = [d for d in self.G.successors(cf) if d not in changed_files]
            if deps:
                result[cf] = deps
        return result

    # ── Stage 2 ───────────────────────────────────────────────────────────────

    def _stage2_semantic_filter(
        self,
        syntactic_deps: dict[str, list[str]],
        diff_symbols: set[str],
        changed_files: list[str],
    ) -> list[SymbolDependency]:
        all_deps: set[str] = set()
        for deps in syntactic_deps.values():
            all_deps.update(deps)

        result: list[SymbolDependency] = []
        for dep_file in all_deps:
            exported = self._exported_symbols(dep_file)
            used = [
                s for s in exported
                if s in diff_symbols
                and len(s) >= self._min_len
                and s not in _NOISE_SYMBOLS
            ]
            if used:
                hop = self._min_hop_distance(changed_files, dep_file)
                result.append(SymbolDependency(
                    file=dep_file,
                    symbols_used=used,
                    centrality=self.centrality.get(dep_file, 0.0),
                    hop_distance=hop,
                ))
        return result

    # ── Stage 3 ───────────────────────────────────────────────────────────────

    def _stage3_constraint_bearing(
        self,
        semantic_deps: list[SymbolDependency],
        annotation_symbols: set[str],
    ) -> list[SymbolDependency]:
        enriched: list[SymbolDependency] = []
        for dep in semantic_deps:
            is_cb, ct = self._classify_dep(dep, annotation_symbols)
            enriched.append(SymbolDependency(
                file=dep.file,
                symbols_used=dep.symbols_used,
                centrality=dep.centrality,
                hop_distance=dep.hop_distance,
                is_constraint_bearing=is_cb,
                constraint_type=ct,
            ))
        return enriched

    def _classify_dep(
        self,
        dep: SymbolDependency,
        annotation_symbols: set[str],
    ) -> tuple[bool, str]:
        """Dispatch to language-specific constraint classifier."""
        if self.language == "python":
            # Annotation overlap → higher confidence, then structural check
            ann_overlap = [s for s in dep.symbols_used if s in annotation_symbols]
            if ann_overlap:
                is_cb, ct = _classify_constraint_python(dep.file, self.repo_path)
                if is_cb:
                    return is_cb, ct
            # Lifecycle coupling
            lc = [s for s in dep.symbols_used if _LIFECYCLE_RE.search(s)]
            if lc:
                return True, "lifecycle"
            # High centrality + multiple symbols
            if dep.centrality > 0.05 and len(dep.symbols_used) >= 2:
                return True, "cross_file_method"
            return False, ""

        elif self.language == "go":
            # Go: any imported file that defines interfaces is constraint-bearing
            # regardless of annotation overlap (Go uses structural typing)
            is_cb, ct = _classify_constraint_go(dep.file, self.repo_path, self._parser)
            if is_cb:
                return is_cb, ct
            # Compliance check in the changed file is also a strong signal
            for cf in [dep.file]:
                if hasattr(self._parser, "has_interface_compliance_checks"):
                    if self._parser.has_interface_compliance_checks(cf):
                        return True, "compliance_check"
            # Lifecycle coupling
            lc = [s for s in dep.symbols_used if _LIFECYCLE_RE.search(s)]
            if lc:
                return True, "lifecycle"
            return False, ""

        elif self.language == "typescript":
            ann_overlap = [s for s in dep.symbols_used if s in annotation_symbols]
            if ann_overlap:
                is_cb, ct = _classify_constraint_ts(dep.file, self.repo_path, self._parser)
                if is_cb:
                    return is_cb, ct
            # Filename heuristic
            fname = Path(dep.file).name.lower()
            if any(kw in fname for kw in ("type", "types", "interface", "model")):
                return True, "type_file"
            if dep.centrality > 0.02 and len(dep.symbols_used) >= 2:
                return True, "cross_file_method"
            return False, ""

        return False, ""

    # ── Stage 4 ───────────────────────────────────────────────────────────────

    def _stage4_irreducibility(
        self,
        deps: list[SymbolDependency],
        diff_symbols: set[str],
        changed_files: list[str],
    ) -> float:
        if not deps:
            return 0.0
        if self.language == "go":
            return self._irr_go(deps)
        return self._irr_symbolic(deps, diff_symbols, changed_files)

    def _irr_symbolic(
        self,
        deps: list[SymbolDependency],
        diff_symbols: set[str],
        changed_files: list[str],
    ) -> float:
        """
        Python/TS: fractional score based on symbol overlap + structural boosts.
        """
        if not diff_symbols:
            return 0.0

        total_in_diff = 0
        for dep in deps:
            exported = self._exported_symbols(dep.file)
            total_in_diff += sum(
                1 for s in exported
                if s in diff_symbols and len(s) >= self._min_len
                and s not in _NOISE_SYMBOLS
            )

        base = min(1.0, total_in_diff / max(1, len(diff_symbols)) * 3.0)
        max_hops = max((d.hop_distance for d in deps), default=1)
        hop_boost = min(0.25, (max_hops - 1) * 0.08)
        cb_count = sum(1 for d in deps if d.is_constraint_bearing)
        cb_boost = min(0.20, cb_count * 0.07)
        avg_centrality = sum(d.centrality for d in deps) / len(deps)
        centrality_boost = min(0.15, avg_centrality * 2.0)

        return min(1.0, round(base + hop_boost + cb_boost + centrality_boost, 4))

    def _irr_go(self, deps: list[SymbolDependency]) -> float:
        """
        Go-specific irreducibility: based on interface count + structural weight.

        Go's coupling is behavioural not lexical — a file that defines
        exported interfaces is always a hard dependency for anything that
        implements or calls those interfaces, even if the interface name
        doesn't literally appear in the diff text.
        """
        if not deps:
            return 0.0

        interface_deps = []
        struct_deps = []
        for dep in deps:
            ifaces = self._parser.get_interface_names(dep.file)
            structs = self._parser.get_struct_names(dep.file)
            if ifaces:
                interface_deps.append(dep)
            elif structs:
                struct_deps.append(dep)

        n_interface = len(interface_deps)
        n_struct = len(struct_deps)
        n_total = len(deps)

        # Base: interface files are strong constraints; struct files are moderate
        base = min(0.55, n_interface * 0.18 + n_struct * 0.07)

        # Centrality: high-centrality deps are more architecturally load-bearing
        avg_centrality = sum(d.centrality for d in deps) / max(1, n_total)
        centrality_boost = min(0.25, avg_centrality * 2.5)

        # Scale: more required files = more coupling
        scale_boost = min(0.20, n_total * 0.035)

        # Symbol overlap still contributes as a secondary signal
        symbol_boost = min(0.15, sum(len(d.symbols_used) for d in deps) * 0.01)

        return min(1.0, round(base + centrality_boost + scale_boost + symbol_boost, 4))

    # ── Public API ────────────────────────────────────────────────────────────

    def analyze(
        self,
        changed_files: list[str],
        diff_text: str,
        min_irr: float = 0.20,
    ) -> ContextDependencyMap:
        diff_symbols = _symbols_from_diff(diff_text, self._min_len)
        annotation_symbols = _annotation_symbols_from_diff(diff_text)

        syntactic = self._stage1_syntactic_deps(changed_files)
        semantic = self._stage2_semantic_filter(syntactic, diff_symbols, changed_files)
        enriched = self._stage3_constraint_bearing(semantic, annotation_symbols)

        # Keep all enriched deps — remove the >= 2 symbol filter that was
        # dropping valid single-constraint deps
        final_deps = enriched if enriched else semantic

        irr_score = self._stage4_irreducibility(final_deps, diff_symbols, changed_files)
        required_files = [d.file for d in final_deps]
        max_hops = max((d.hop_distance for d in final_deps), default=0)
        cb_files = [d.file for d in final_deps if d.is_constraint_bearing]
        ct_found = list({d.constraint_type for d in final_deps if d.constraint_type})

        return ContextDependencyMap(
            changed_files=changed_files,
            required_context_files=required_files,
            required_context_details=final_deps,
            context_distance_hops=max_hops,
            irreducibility_score=irr_score,
            symbol_dependencies=final_deps,
            constraint_bearing_files=cb_files,
            constraint_types_found=ct_found,
        )