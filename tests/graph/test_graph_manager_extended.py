"""Tests for extended GraphManager features (Graph-of-Graphs)."""

from pathlib import Path
import json
import networkx as nx
from depanalyzer.graph import GraphManager
from depanalyzer.graph.models.schema import NodeType

def test_external_graph_registration(tmp_path: Path) -> None:
    """Test registering an external graph path."""
    manager = GraphManager(graph_id="main", root_path=tmp_path)
    ext_path = tmp_path / "ext_graph.json"
    
    manager.register_external_graph("ext1", ext_path)
    assert manager._external_graph_registry["ext1"] == ext_path

def test_mount_external_node(tmp_path: Path) -> None:
    """Test creating a proxy node pointing to an external graph."""
    manager = GraphManager(graph_id="main", root_path=tmp_path)
    
    manager.mount_external_node(
        node_id="//proxy/node",
        external_graph_id="ext1",
        label="Proxy Node"
    )
    
    node_data = manager.get_node("//proxy/node")
    assert node_data is not None
    assert node_data["type"] == NodeType.PROXY.value
    assert node_data["external_graph_id"] == "ext1"

def test_lazy_load_subgraph(tmp_path: Path) -> None:
    """Test lazy loading of an external graph from disk."""
    # 1. Create an external graph and save it
    ext_manager = GraphManager(graph_id="ext1", root_path=tmp_path)
    ext_manager.add_node("//ext/node1", NodeType.CODE)
    ext_path = tmp_path / "ext_graph.json"
    ext_manager.save(ext_path)
    
    # 2. Create main graph and register external graph
    main_manager = GraphManager(graph_id="main", root_path=tmp_path)
    main_manager.register_external_graph("ext1", ext_path)
    
    # 3. Load it
    loaded_manager = main_manager.lazy_load_subgraph("ext1")
    
    assert loaded_manager is not None
    assert loaded_manager.graph_id == "default"  # Default init ID unless saved metadata overrides
    # Note: load() creates a new manager, and the saved JSON might not store the graph_id in the networkx attrs in a way load() restores automatically unless specifically handled.
    # The current save/load implementation stores metadata but might not restore the graph_id attribute of the manager class itself from JSON content easily without looking at metadata.
    
    assert loaded_manager.has_node("//ext/node1")

def test_lazy_load_missing_graph(tmp_path: Path) -> None:
    """Test lazy loading a non-existent or unregistered graph."""
    manager = GraphManager(graph_id="main", root_path=tmp_path)
    
    # Unregistered
    assert manager.lazy_load_subgraph("unknown") is None
    
    # Registered but missing file
    manager.register_external_graph("missing", tmp_path / "missing.json")
    assert manager.lazy_load_subgraph("missing") is None
