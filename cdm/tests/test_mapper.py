"""
cdm/tests/test_mapper.py

Unit tests for the Context Dependency Mapper.

Tests cover:
  - Diff symbol extraction (including context lines and annotations)
  - Import graph construction (per language)
  - Signal computation (import, type, structural, test proximity)
  - CFRD computation (pairwise coupling)
  - RFS computation (enhanced with CFRD)
  - End-to-end analyze() for known task snapshots

USAGE:
  pytest cdm/tests/ -v
  pytest cdm/tests/test_mapper.py::test_cfrd_star_vs_mesh -v
"""

from __future__ import annotations

import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

                                  
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cdm.mapper import (
    NOISE_SYMBOLS,
    _extract_annotation_symbols,
    _extract_diff_symbols,
    ContextDependencyMapper,
    SignalBreakdown,
)


                                                                                

class TestDiffSymbolExtraction(unittest.TestCase):

    def test_extracts_from_added_lines(self):
        diff = textwrap.dedent("""
            --- a/foo.py
            +++ b/foo.py
            @@ -1,3 +1,4 @@
            +    result = MyClass(config=ConfigObject())
        """)
        syms = _extract_diff_symbols(diff)
        self.assertIn("MyClass", syms)
        self.assertIn("ConfigObject", syms)

    def test_extracts_from_removed_lines(self):
        diff = textwrap.dedent("""
            --- a/foo.py
            +++ b/foo.py
            @@ -1,3 +1,3 @@
            -    old_value = LegacyHandler(data)
            +    new_value = ModernHandler(data)
        """)
        syms = _extract_diff_symbols(diff)
        self.assertIn("LegacyHandler", syms)                              
        self.assertIn("ModernHandler", syms)

    def test_extracts_from_context_lines(self):
        diff = textwrap.dedent("""
            --- a/foo.py
            +++ b/foo.py
            @@ -1,5 +1,5 @@
             class FooClass:
             def __init__(self):
            -    self.handler = OldHandler()
            +    self.handler = NewHandler()
        """)
        syms = _extract_diff_symbols(diff)
        self.assertIn("FooClass", syms)                

    def test_excludes_noise_symbols(self):
        diff = "+    self = None  # return nil func interface struct\n"
        syms = _extract_diff_symbols(diff)
        for noise in ("self", "nil", "func", "interface", "struct"):
            self.assertNotIn(noise, syms)

    def test_minimum_symbol_length(self):
        diff = "+    if ok:\n"
        syms = _extract_diff_symbols(diff, min_len=4)
        self.assertNotIn("ok", syms)             
        self.assertNotIn("if", syms)

    def test_skips_diff_headers(self):
        diff = "diff --git a/foo.py b/foo.py\nindex abc..def 100644\n"
        syms = _extract_diff_symbols(diff)
        self.assertNotIn("diff", syms)
        self.assertNotIn("index", syms)


class TestAnnotationSymbolExtraction(unittest.TestCase):

    def test_return_type_annotation(self):
        diff = "+def get_session() -> SessionMixin:\n"
        syms = _extract_annotation_symbols(diff)
        self.assertIn("SessionMixin", syms)

    def test_parameter_type_annotation(self):
        diff = "+def init(app: FlaskApp, config: ConfigType) -> None:\n"
        syms = _extract_annotation_symbols(diff)
        self.assertIn("FlaskApp", syms)
        self.assertIn("ConfigType", syms)

    def test_only_added_lines(self):
        diff = "-def old_func(ctx: OldContext) -> OldReturn:\n"
        syms = _extract_annotation_symbols(diff)
        self.assertNotIn("OldContext", syms)                 
        self.assertNotIn("OldReturn", syms)


                                                                                

class TestCFRD(unittest.TestCase):
    """
    Test the formal CFRD computation: CFRD(F) = (1/n(n-1)) Σ ρ(fi,fj)·ι(fi,fj)

    We create mock mappers with controlled import graphs and symbol sets
    to verify that CFRD behaves as expected for known topologies.
    """

    def _make_mapper_with_graph(self, edges, symbols):
        """Create a ContextDependencyMapper mock with a specific import graph."""
        import networkx as nx
        from cdm.mapper import ContextDependencyMapper

                                                                       
        mapper = object.__new__(ContextDependencyMapper)
        G = nx.DiGraph()
        G.add_edges_from(edges)
        mapper.G = G
        mapper.language = "python"
        mapper._min_sym_len = 4
        mapper._sym_cache = symbols                       
        mapper._centrality = {}
        return mapper

    def test_single_file_returns_zero(self):
        mapper = self._make_mapper_with_graph(
            [], {"file_a.py": {"FooClass"}}
        )
        cfrd = mapper._compute_cfrd(["file_a.py"])
        self.assertEqual(cfrd, 0.0)

    def test_two_files_no_shared_symbols(self):
        mapper = self._make_mapper_with_graph(
            [("file_a.py", "file_b.py")],
            {"file_a.py": {"ClassA"}, "file_b.py": {"ClassB"}}
        )
        cfrd = mapper._compute_cfrd(["file_a.py", "file_b.py"])
                                                               
        self.assertEqual(cfrd, 0.0)

    def test_two_files_with_shared_symbols(self):
        mapper = self._make_mapper_with_graph(
            [("file_a.py", "file_b.py")],
            {"file_a.py": {"SharedClass", "OtherClass"},
             "file_b.py": {"SharedClass", "ThirdClass"}}
        )
        cfrd = mapper._compute_cfrd(["file_a.py", "file_b.py"])
                                           
        self.assertGreater(cfrd, 0.0)
        self.assertLessEqual(cfrd, 1.0)

    def test_star_topology_lower_cfrd_than_mesh(self):
        """
        Star topology: all files connect to hub only, no cross-connections.
        Mesh topology: every file connects to every other file.
        CFRD should be lower for star (files don't cross-couple)
        and higher for mesh.
        """
                                                           
        star_mapper = self._make_mapper_with_graph(
            [("file_a.py", "hub.py"),
             ("file_b.py", "hub.py"),
             ("file_c.py", "hub.py")],
            {"file_a.py": {"ClassA"}, "file_b.py": {"ClassB"},
             "file_c.py": {"ClassC"}, "hub.py": {"SharedClass"}}
        )
        star_cfrd = star_mapper._compute_cfrd(
            ["file_a.py", "file_b.py", "file_c.py", "hub.py"]
        )

                                                                     
        mesh_symbols = {
            "mesh_a.py": {"Alpha", "Beta"},
            "mesh_b.py": {"Alpha", "Gamma"},
            "mesh_c.py": {"Beta", "Gamma"},
        }
        mesh_mapper = self._make_mapper_with_graph(
            [("mesh_a.py", "mesh_b.py"), ("mesh_a.py", "mesh_c.py"),
             ("mesh_b.py", "mesh_a.py"), ("mesh_b.py", "mesh_c.py"),
             ("mesh_c.py", "mesh_a.py"), ("mesh_c.py", "mesh_b.py")],
            mesh_symbols
        )
        mesh_cfrd = mesh_mapper._compute_cfrd(["mesh_a.py", "mesh_b.py", "mesh_c.py"])

        self.assertLessEqual(star_cfrd, mesh_cfrd,
            f"Star CFRD ({star_cfrd}) should be ≤ mesh CFRD ({mesh_cfrd})")

    def test_cfrd_bounded_to_unit_interval(self):
        mapper = self._make_mapper_with_graph(
            [("a.py", "b.py"), ("b.py", "c.py"), ("c.py", "a.py"),
             ("a.py", "c.py"), ("b.py", "a.py")],
            {"a.py": {"X", "Y", "Z"}, "b.py": {"X", "Y", "W"},
             "c.py": {"Z", "W", "V"}}
        )
        cfrd = mapper._compute_cfrd(["a.py", "b.py", "c.py"])
        self.assertGreaterEqual(cfrd, 0.0)
        self.assertLessEqual(cfrd, 1.0)


                                                                                

class TestRFS(unittest.TestCase):

    def _make_minimal_details(self, n_files: int, hop_distance: int,
                               structural_score: float = 0.0) -> list[SignalBreakdown]:
        return [
            SignalBreakdown(
                file=f"file_{i}.py",
                import_score=1.0,
                type_score=0.5,
                structural_score=structural_score,
                test_proximity=0.0,
                rcs=0.5,
                hop_distance=hop_distance,
                exclusive_symbols=["SomeSymbol"],
                structural_reason="interface" if structural_score > 0 else "",
            )
            for i in range(n_files)
        ]

    def _make_mapper(self) -> ContextDependencyMapper:
        import networkx as nx
        mapper = object.__new__(ContextDependencyMapper)
        mapper.G = nx.DiGraph()
        mapper.language = "python"
        mapper._min_sym_len = 4
        mapper._sym_cache = {}
        mapper._centrality = {}
        return mapper

    def test_zero_details_returns_zero(self):
        mapper = self._make_mapper()
        rfs = mapper._compute_rfs([], set(), [], 0.0)
        self.assertEqual(rfs, 0.0)

    def test_rfs_increases_with_hop_distance(self):
        mapper = self._make_mapper()
        rfs_1hop = mapper._compute_rfs(
            self._make_minimal_details(2, 1), {"SomeSymbol"}, ["changed.py"], 0.0
        )
        rfs_3hop = mapper._compute_rfs(
            self._make_minimal_details(2, 3), {"SomeSymbol"}, ["changed.py"], 0.0
        )
        self.assertLess(rfs_1hop, rfs_3hop)

    def test_rfs_increases_with_cfrd(self):
        mapper = self._make_mapper()
        details = self._make_minimal_details(2, 1)
        rfs_low_cfrd  = mapper._compute_rfs(details, {"SomeSymbol"}, ["changed.py"], 0.0)
        rfs_high_cfrd = mapper._compute_rfs(details, {"SomeSymbol"}, ["changed.py"], 0.8)
        self.assertLess(rfs_low_cfrd, rfs_high_cfrd,
            "RFS should increase with CFRD (CFRD is a component)")

    def test_rfs_bounded(self):
        mapper = self._make_mapper()
        details = self._make_minimal_details(10, 5, structural_score=1.0)
        rfs = mapper._compute_rfs(details, {"A", "B", "C", "D", "E"}, [], 1.0)
        self.assertGreaterEqual(rfs, 0.0)
        self.assertLessEqual(rfs, 1.0)


                                                                                

class TestContextDependencyMapIntegration(unittest.TestCase):
    """
    Smoke tests for the full analyze() pipeline using mock parsers.
    These tests don't require actual repositories.
    """

    @patch("cdm.mapper.ContextDependencyMapper._build_graph")
    def test_analyze_empty_diff_returns_empty_map(self, mock_build):
        import networkx as nx
        G = nx.DiGraph()
        G.add_node("src/foo.py")
        mock_parser = MagicMock()
        mock_parser.build_import_graph.return_value = G
        mock_parser.get_exported_symbols.return_value = set()
        mock_build.return_value = (mock_parser, G)

        mapper = ContextDependencyMapper.__new__(ContextDependencyMapper)
        mapper.repo_path = Path("/tmp/fake")
        mapper.language = "python"
        mapper.subtree = None
        mapper._min_sym_len = 4
        mapper.G = G
        mapper._centrality = {}
        mapper._sym_cache = {}
        mapper._parser = mock_parser

        result = mapper.analyze(["src/foo.py"], "")
        self.assertEqual(result.required_context_files, [])
        self.assertEqual(result.irreducibility_score, 0.0)
        self.assertEqual(result.cfrd, 0.0)

    def test_cdm_output_dict_has_required_keys(self):
        """Verify to_dict() always includes CFRD and RFS."""
        from cdm.mapper import ContextDependencyMap
        cdm = ContextDependencyMap(
            changed_files=["a.py"],
            required_context_files=["b.py"],
            signal_details=[],
            context_distance_hops=1,
            irreducibility_score=0.42,
            rfs=0.35,
            cfrd=0.28,
            constraint_bearing_files=[],
            constraint_types=[],
        )
        d = cdm.to_dict()
        self.assertIn("cfrd", d)
        self.assertIn("rfs", d)
        self.assertIn("irreducibility_score", d)
        self.assertEqual(d["cfrd"], 0.28)
        self.assertEqual(d["rfs"], 0.35)


if __name__ == "__main__":
    unittest.main(verbosity=2)