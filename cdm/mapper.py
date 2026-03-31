"""
Context Dependency Mapper.

This module identifies required context files for a change by combining
four signals per candidate file: import distance, symbol/type relevance,
structural coupling, and test proximity.

Files above the relevance threshold are included in required context.
Task-level metrics such as irreducibility score, RFS, and CFRD are then
computed from the selected files and graph relationships.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import networkx as nx

                                                                                

                                                                  
RELEVANCE_THRESHOLD = 0.18

                                         
W_IMPORT     = 0.30
W_TYPE       = 0.35
W_STRUCTURAL = 0.25
W_TEST       = 0.10

                                                               
W_DEPTH    = 0.30
W_IFACE    = 0.25
W_BREADTH  = 0.20
W_LOCAL    = 0.15
W_CFRD     = 0.10                                                                      

                                                                
NOISE_SYMBOLS: frozenset[str] = frozenset({
                       
    "self", "cls", "this", "super", "None", "nil", "null", "true", "false",
    "string", "int", "float", "bool", "any", "void", "error", "err",
    "ctx", "req", "res", "resp", "request", "response", "context",
    "data", "result", "value", "values", "key", "keys", "name",
    "path", "args", "kwargs", "opts", "options",
            
    "object", "type", "list", "dict", "set", "tuple",
                
    "string", "number", "boolean", "object", "unknown", "never",
        
    "func", "struct", "interface", "map", "chan", "make", "new", "len", "cap",
})

                                         
MIN_SYM_LEN = 4


                                                                                

@dataclass
class SignalBreakdown:
    """Four-signal breakdown for a single candidate file."""
    file: str
    import_score: float     = 0.0
    type_score: float       = 0.0
    structural_score: float = 0.0
    test_proximity: float   = 0.0
    rcs: float              = 0.0                
    hop_distance: int       = 1
    exclusive_symbols: list[str] = field(default_factory=list)
    structural_reason: str  = ""                               


@dataclass
class ContextDependencyMap:
    changed_files: list[str]
    required_context_files: list[str]
    signal_details: list[SignalBreakdown]
    context_distance_hops: int
    irreducibility_score: float
    rfs: float                                                                  
    cfrd: float                                                              
    constraint_bearing_files: list[str]
    constraint_types: list[str]

    def is_valid_task(self, min_irr: float = 0.20) -> bool:
        return (
            len(self.required_context_files) > 0
            and self.irreducibility_score >= min_irr
        )

    def to_dict(self) -> dict:
        return {
            "changed_files": self.changed_files,
            "required_context_files": self.required_context_files,
            "context_distance_hops": self.context_distance_hops,
            "irreducibility_score": round(self.irreducibility_score, 4),
            "rfs": round(self.rfs, 4),
            "cfrd": round(self.cfrd, 4),
            "constraint_bearing_files": self.constraint_bearing_files,
            "constraint_types_found": self.constraint_types,
            "required_context_details": [
                {
                    "file": d.file,
                    "rcs": round(d.rcs, 4),
                    "import_score": round(d.import_score, 4),
                    "type_score": round(d.type_score, 4),
                    "structural_score": round(d.structural_score, 4),
                    "test_proximity": round(d.test_proximity, 4),
                    "hop_distance": d.hop_distance,
                    "exclusive_symbols": d.exclusive_symbols[:8],
                    "structural_reason": d.structural_reason,
                    "is_constraint_bearing": d.rcs > 0.35,
                }
                for d in self.signal_details
            ],
        }


                                                                                

def _extract_diff_symbols(diff_text: str, min_len: int = MIN_SYM_LEN) -> set[str]:
    """
    Extract all meaningful identifiers from both added (+) and removed (-)
    lines of a unified diff, plus context lines.  Context lines matter because
    the unchanged surrounding code often references the symbols that make a
    dependency load-bearing.
    """
    symbols: set[str] = set()
    for line in diff_text.splitlines():
        if line.startswith(("+++", "---", "@@", "diff ", "index ")):
            continue
                                           
        code = line[1:] if line and line[0] in ("+", "-", " ") else line
        for tok in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]+)\b", code):
            if len(tok) >= min_len and tok not in NOISE_SYMBOLS:
                symbols.add(tok)
    return symbols


def _extract_annotation_symbols(diff_text: str) -> set[str]:
    """
    Extract symbols that appear in type annotation positions in added lines.
    These carry higher confidence than arbitrary identifier occurrences
    because they directly constrain what types the implementation must use.
    """
    symbols: set[str] = set()
    for line in diff_text.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        code = line[1:]
                                                   
        for m in re.finditer(r"->\s*([A-Za-z_][A-Za-z0-9_\[\], |]*?)(?:\s*:|\s*$)", code):
            for tok in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]+)\b", m.group(1)):
                if len(tok) >= MIN_SYM_LEN and tok not in NOISE_SYMBOLS:
                    symbols.add(tok)
                                                  
        for m in re.finditer(r":\s*([A-Za-z_][A-Za-z0-9_\[\], |]*?)(?:\s*[,)=])", code):
            for tok in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]+)\b", m.group(1)):
                if len(tok) >= MIN_SYM_LEN and tok not in NOISE_SYMBOLS:
                    symbols.add(tok)
    return symbols


                                                                                

class ContextDependencyMapper:
    """
    Multi-signal context dependency mapper.

    Usage:
        mapper = ContextDependencyMapper("data/repos/flask", "python")
        result = mapper.analyze(changed_files, diff_text)
    """

    def __init__(
        self,
        repo_path: str,
        language: str = "python",
        subtree: Optional[str] = None,
    ):
        self.repo_path = Path(repo_path).resolve()
        self.language  = language.lower()
        self.subtree   = subtree
        self._min_sym_len = 3 if self.language == "go" else MIN_SYM_LEN

                                                          
        self._parser, self.G = self._build_graph()
        self._centrality: dict[str, float] = nx.betweenness_centrality(self.G)
        self._sym_cache:  dict[str, set[str]] = {}

                                                                                

    def _build_graph(self) -> tuple:
        if self.language == "python":
            from cdm.languages.python_parser import PythonImportParser
            p = PythonImportParser(str(self.repo_path))
        elif self.language == "typescript":
            from cdm.languages.typescript_parser import TypeScriptImportParser
            p = TypeScriptImportParser(str(self.repo_path), subtree=self.subtree)
        elif self.language == "go":
            from cdm.languages.go_parser import GoImportParser
            p = GoImportParser(str(self.repo_path))
        else:
            raise NotImplementedError(f"Unsupported language: {self.language}")
        return p, p.build_import_graph()

    def _symbols(self, filepath: str) -> set[str]:
        """Cached exported symbol lookup."""
        if filepath not in self._sym_cache:
            self._sym_cache[filepath] = self._parser.get_exported_symbols(filepath)
        return self._sym_cache[filepath]

    def _interfaces(self, filepath: str) -> set[str]:
        """Interface / ABC / Protocol names defined in this file."""
        if hasattr(self._parser, "get_interface_names"):
            return self._parser.get_interface_names(filepath)
        return set()

                                                                                

    def _import_score(self, changed_files: list[str], candidate: str) -> tuple[float, int]:
        """
        Returns (score, min_hops).
        Direct import from any changed file scores 1.0; each additional hop
        multiplies by 0.6 (geometric decay — being two hops away halves the
        signal compared to being one hop away).
        """
        if not self.G.has_node(candidate):
            return 0.0, 99

        min_hops = 99
        for cf in changed_files:
            if not self.G.has_node(cf):
                continue
            try:
                d = nx.shortest_path_length(self.G, cf, candidate)
                min_hops = min(min_hops, d)
            except nx.NetworkXNoPath:
                pass

        if min_hops == 99:
            return 0.0, 99

                                                                         
        score = 0.6 ** (min_hops - 1)
        return round(score, 4), min_hops

                                                                                

    def _type_score(
        self,
        candidate: str,
        diff_symbols: set[str],
        changed_files: list[str],
        annotation_symbols: set[str],
    ) -> tuple[float, list[str]]:
        """
        Returns (score, exclusive_symbols).
        Exclusive symbols are those exported by the candidate but NOT
        re-exported or defined in any changed file.  Only exclusive symbols
        count — this eliminates the common-name noise problem.
        """
        candidate_exports = self._symbols(candidate)

                                                                    
        local_symbols: set[str] = set()
        for cf in changed_files:
            local_symbols.update(self._symbols(cf))

                                                                           
        exclusive = [
            s for s in candidate_exports
            if s in diff_symbols
            and s not in local_symbols
            and len(s) >= self._min_sym_len
            and s not in NOISE_SYMBOLS
        ]

        if not diff_symbols:
            return 0.0, []

                                                                           
                                                          
        annotation_exclusive = [s for s in exclusive if s in annotation_symbols]
        weighted_exclusive = len(exclusive) + len(annotation_exclusive)
        score = min(1.0, weighted_exclusive / max(1, len(diff_symbols)) * 4.0)
        return round(score, 4), exclusive

                                                                                

    def _structural_score(
        self,
        candidate: str,
        diff_symbols: set[str],
        changed_files: list[str],
    ) -> tuple[float, str]:
        """
        Language-specific structural coupling detection.
        Returns (score, human_readable_reason).

        Go: does the candidate define an exported interface whose name
            appears in the diff?  Interface coupling in Go is implicit
            (duck typing), so lexical overlap here means real coupling.

        Python: does the candidate define an ABC, Protocol, or TypedDict
                whose name appears in the diff annotations?

        TypeScript: does the candidate define an exported interface or
                    type alias appearing in diff annotations?
        """
        interfaces = self._interfaces(candidate)
        if not interfaces:
                                                                      
            fname = Path(candidate).name.lower()
            is_types_file = any(kw in fname for kw in (
                "types", "type", "interfaces", "interface",
                "models", "model", "schema", "proto", "mixin",
            ))
            if not is_types_file:
                return 0.0, ""

                                                         
        iface_hits = [i for i in interfaces if i in diff_symbols and i not in NOISE_SYMBOLS]
        if iface_hits:
            reason = f"defines interface(s) used in diff: {', '.join(iface_hits[:3])}"
                                                             
            score = min(1.0, len(iface_hits) * 0.5)
            return round(score, 4), reason

                                                  
        fname = Path(candidate).name.lower()
        if any(kw in fname for kw in ("types", "interfaces", "schema")):
                                                         
            hits = [s for s in self._symbols(candidate) if s in diff_symbols
                    and s not in NOISE_SYMBOLS]
            if hits:
                reason = f"types/interfaces file with {len(hits)} diff symbols"
                return 0.25, reason

        return 0.0, ""

                                                                                

    def _test_proximity(
        self,
        candidate: str,
        changed_files: list[str],
    ) -> float:
        """
        Files that are imported by the test files covering the changed code
        are almost always semantically load-bearing.  This signal is weaker
        but useful as a tie-breaker.

        We detect test files by filename pattern, find which non-test files
        they import, and give credit if the candidate is in that set.
        """
        test_imports: set[str] = set()

        for node in self.G.nodes():
            is_test = (
                "test" in node.lower()
                or "spec" in node.lower()
                or node.endswith("_test.go")
            )
            if not is_test:
                continue

                                                                   
            imports_changed = any(
                self.G.has_edge(node, cf) or self.G.has_edge(cf, node)
                for cf in changed_files
            )
            if imports_changed:
                test_imports.update(self.G.successors(node))
                test_imports.update(self.G.predecessors(node))

        return 1.0 if candidate in test_imports else 0.0

                                                                                

    def _compute_cfrd(
        self,
        all_task_files: list[str],                               
    ) -> float:
        """
        CFRD(F) = (1 / n(n-1)) × Σ_{i≠j} ρ(fi, fj) · ι(fi, fj)

        ρ(fi, fj)  = normalised shortest-path distance in the import graph
                     between fi and fj.  Normalised by dividing by the
                     graph diameter (max observed shortest path, capped at 5
                     to handle disconnected nodes conservatively).

        ι(fi, fj)  = interaction complexity = |shared_exclusive_symbols| /
                     max(1, |exported(fi)| + |exported(fj)|)
                     Measures how tightly coupled two files are via their
                     shared exported symbol surface.

        Returns 0.0 for fewer than 2 files.
        """
        n = len(all_task_files)
        if n < 2:
            return 0.0

                                           
        exports: dict[str, set[str]] = {f: self._symbols(f) for f in all_task_files}

                                                           
        max_path = 1
        for fi in all_task_files:
            for fj in all_task_files:
                if fi == fj or not self.G.has_node(fi) or not self.G.has_node(fj):
                    continue
                try:
                    d = nx.shortest_path_length(self.G, fi, fj)
                    max_path = max(max_path, d)
                except nx.NetworkXNoPath:
                    pass
        diameter = max(1, min(max_path, 5))

        total = 0.0
        pairs = 0
        for i, fi in enumerate(all_task_files):
            for fj in all_task_files[i + 1:]:
                pairs += 1

                                             
                rho = 0.0
                if self.G.has_node(fi) and self.G.has_node(fj):
                    try:
                        d = nx.shortest_path_length(self.G, fi, fj)
                        rho = d / diameter
                    except nx.NetworkXNoPath:
                        rho = 1.0                               

                                                                        
                sym_i = exports.get(fi, set())
                sym_j = exports.get(fj, set())
                shared = sym_i & sym_j - NOISE_SYMBOLS
                denom = max(1, len(sym_i) + len(sym_j))
                iota = len(shared) / denom * 2                                   

                total += rho * iota

        if pairs == 0:
            return 0.0
                                                                           
        return round(min(1.0, total / pairs), 4)

                                                                                

    def _compute_rfs(
        self,
        required_details: list[SignalBreakdown],
        diff_symbols: set[str],
        changed_files: list[str],
        cfrd: float,
    ) -> float:
        """
        RFS = 0.30×depth + 0.25×iface + 0.20×breadth
            + 0.15×locality_deficit + 0.10×CFRD
        """
        if not required_details:
            return 0.0

        avg_hops = sum(d.hop_distance for d in required_details) / len(required_details)
        depth = min(1.0, (avg_hops - 1) / 4.0)

        iface_count = sum(1 for d in required_details if d.structural_score > 0.0)
        iface = min(1.0, iface_count / 5.0)

        breadth = min(1.0, len(required_details) / 8.0)

        local_syms: set[str] = set()
        for cf in changed_files:
            local_syms.update(self._symbols(cf))
        external_count = sum(1 for s in diff_symbols if s not in local_syms)
        locality_deficit = external_count / max(1, len(diff_symbols))

        rfs = (W_DEPTH * depth + W_IFACE * iface + W_BREADTH * breadth
               + W_LOCAL * locality_deficit + W_CFRD * cfrd)
        return round(min(1.0, rfs), 4)

                                                                                

    def analyze(
        self,
        changed_files: list[str],
        diff_text: str,
        min_irr: float = 0.20,
    ) -> ContextDependencyMap:
        """
        Run multi-signal analysis and return a ContextDependencyMap.
        """
        diff_symbols = _extract_diff_symbols(diff_text, self._min_sym_len)
        annotation_symbols = _extract_annotation_symbols(diff_text)

                                                                                 
                                                 
        changed_set = set(changed_files)
        candidates: set[str] = set()
        for cf in changed_files:
            if not self.G.has_node(cf):
                continue
            for depth in range(1, 4):
                for _, node in nx.bfs_edges(self.G, cf, depth_limit=depth):
                    if node not in changed_set:
                        candidates.add(node)

                                                   
        details: list[SignalBreakdown] = []
        for candidate in candidates:
            imp_score, hops = self._import_score(changed_files, candidate)
            if imp_score == 0.0:
                continue                                  

            typ_score, excl_syms = self._type_score(
                candidate, diff_symbols, changed_files, annotation_symbols
            )
            str_score, str_reason = self._structural_score(
                candidate, diff_symbols, changed_files
            )
            tst_score = self._test_proximity(candidate, changed_files)

            rcs = (W_IMPORT * imp_score + W_TYPE * typ_score +
                   W_STRUCTURAL * str_score + W_TEST * tst_score)

            if rcs < RELEVANCE_THRESHOLD:
                continue

            details.append(SignalBreakdown(
                file=candidate,
                import_score=imp_score,
                type_score=typ_score,
                structural_score=str_score,
                test_proximity=tst_score,
                rcs=round(rcs, 4),
                hop_distance=hops,
                exclusive_symbols=excl_syms,
                structural_reason=str_reason,
            ))

                                 
        details.sort(key=lambda d: d.rcs, reverse=True)
        required_files = [d.file for d in details]

                                                       
        rcs_sum = sum(d.rcs for d in details)
        irr = round(math.tanh(2.5 * rcs_sum / max(1, len(details))), 4) if details else 0.0

                                                                  
        max_hops = max((d.hop_distance for d in details), default=0)

                                                                             
        all_task_files = list(set(changed_files) | set(required_files))
        cfrd = self._compute_cfrd(all_task_files)

                                            
        rfs = self._compute_rfs(details, diff_symbols, changed_files, cfrd)

                                                                 
        cb_files = [d.file for d in details if d.structural_score > 0.0]
        ct_found = list({
            ("interface" if "interface" in d.structural_reason else
             "type_file" if "types" in d.structural_reason else
             "cross_file_method")
            for d in details if d.structural_reason
        })

        return ContextDependencyMap(
            changed_files=changed_files,
            required_context_files=required_files,
            signal_details=details,
            context_distance_hops=max_hops,
            irreducibility_score=irr,
            rfs=rfs,
            cfrd=cfrd,
            constraint_bearing_files=cb_files,
            constraint_types=ct_found,
        )
