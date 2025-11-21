"""Tests for GlobalDAG cycle handling."""

from pathlib import Path

import networkx as nx

from depanalyzer.graph.global_dag import GlobalDAG


def _fresh_global_dag(tmp_path: Path) -> GlobalDAG:
    """Return a fresh GlobalDAG instance isolated to a temp directory."""
    GlobalDAG._instance = None  # type: ignore[attr-defined]
    GlobalDAG._initialized = False  # type: ignore[attr-defined]
    return GlobalDAG(cache_dir=tmp_path)


def test_global_dag_acyclic_view_collapses_cycle(tmp_path: Path) -> None:
    """Cycle should be represented by a virtual cluster node."""
    dag = _fresh_global_dag(tmp_path)
    dag.add_dependency("X", "A")
    dag.add_dependency("A", "B")
    dag.add_dependency("B", "A")
    dag.add_dependency("B", "Y")

    acyclic, removed = dag.get_acyclic_view()

    assert removed >= 1
    cluster_nodes = [
        node_id for node_id, attrs in acyclic.nodes(data=True)
        if attrs.get("type") in {"scc_cluster", "code_scc_cluster"}
    ]
    assert cluster_nodes, "Expected a virtual SCC cluster node"
    cluster = cluster_nodes[0]

    assert acyclic.has_edge("X", cluster)
    assert acyclic.has_edge(cluster, "Y")
    assert acyclic.has_edge(cluster, "A")
    assert acyclic.has_edge(cluster, "B")
    assert not acyclic.has_edge("A", "B")
    assert not acyclic.has_edge("B", "A")
    assert nx.is_directed_acyclic_graph(acyclic)
