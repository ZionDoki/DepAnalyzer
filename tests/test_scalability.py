"""Tests for Phase 3 scalability features."""

import unittest
import shutil
import tempfile
import networkx as nx
from pathlib import Path

from depanalyzer.graph.storage.sqlite_backend import SQLiteGraphBackend
from depanalyzer.graph.ops.composite import CompositeGraph
from depanalyzer.graph.core.backend import NetworkXBackend

class TestSQLiteBackend(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.tmp_dir) / "graph.db"
        self.backend = SQLiteGraphBackend(self.db_path)

    def tearDown(self):
        self.backend.close()
        shutil.rmtree(self.tmp_dir)

    def test_add_get_node(self):
        self.backend.add_node("n1", type="test", score=0.9)
        self.assertTrue(self.backend.has_node("n1"))
        data = self.backend.get_node_data("n1")
        self.assertEqual(data["type"], "test")
        self.assertEqual(data["score"], 0.9)

    def test_add_get_edge(self):
        self.backend.add_node("n1")
        self.backend.add_node("n2")
        self.backend.add_edge("n1", "n2", kind="link", weight=1)
        
        self.assertTrue(self.backend.has_edge("n1", "n2"))
        
        # Test iterator
        edges = list(self.backend.edges(data=True))
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0][0], "n1")
        self.assertEqual(edges[0][1], "n2")
        self.assertEqual(edges[0][2]["kind"], "link")

class TestCompositeGraph(unittest.TestCase):
    def test_iteration(self):
        # Main graph (in-memory)
        main_be = NetworkXBackend()
        main_be.add_node("main_root", type="root")
        
        # Dep graph (SQLite)
        tmp_dir = tempfile.mkdtemp()
        db_path = Path(tmp_dir) / "dep.db"
        dep_be = SQLiteGraphBackend(db_path)
        dep_be.add_node("lib_node", type="lib")
        
        composite = CompositeGraph(main_be, "main")
        composite.add_dependency("dep1", dep_be, "dep:v1:")
        
        # Check nodes
        nodes = dict(composite.nodes(data=True))
        self.assertIn("main_root", nodes)
        self.assertIn("dep:v1:lib_node", nodes)
        
        self.assertEqual(nodes["dep:v1:lib_node"]["orig_id"], "lib_node")
        
        # Cleanup
        dep_be.close()
        shutil.rmtree(tmp_dir)

if __name__ == "__main__":
    unittest.main()
