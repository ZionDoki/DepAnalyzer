"""Unit tests for export-time cycle handling."""

import networkx as nx

from depanalyzer.graph.io import break_cycles


def _find_cluster_node(graph: nx.MultiDiGraph) -> str:
    """Return the first SCC cluster node id in the graph."""
    for node_id, attrs in graph.nodes(data=True):
        if attrs.get("type") in {"scc_cluster", "code_scc_cluster"}:
            return str(node_id)
    raise AssertionError("No cluster node found")


def test_break_cycles_creates_virtual_node_and_rewires_edges() -> None:
    """Cycles are replaced by a virtual node attached to members."""
    graph = nx.MultiDiGraph()
    graph.add_edge("x", "a", kind="depends_on")
    graph.add_edge("a", "b", kind="depends_on")
    graph.add_edge("b", "c", kind="depends_on")
    graph.add_edge("c", "a", kind="depends_on")
    graph.add_edge("c", "y", kind="depends_on")

    acyclic, removed = break_cycles(graph)

    cluster = _find_cluster_node(acyclic)
    assert removed == 3
    assert acyclic.has_edge("x", "a")
    assert acyclic.has_edge("c", "y")
    assert acyclic.has_edge(cluster, "a")
    assert acyclic.has_edge(cluster, "b")
    assert acyclic.has_edge(cluster, "c")
    assert not acyclic.has_edge("a", "b")
    assert not acyclic.has_edge("b", "c")
    assert not acyclic.has_edge("c", "a")


def test_break_cycles_handles_self_loop_only() -> None:
    """Single-node SCCs drop self loops without creating a cluster."""
    graph = nx.MultiDiGraph()
    graph.add_edge("solo", "solo", kind="depends_on")
    graph.add_edge("origin", "solo", kind="depends_on")

    acyclic, removed = break_cycles(graph)

    assert removed == 1
    assert acyclic.has_edge("origin", "solo")
    assert not acyclic.has_edge("solo", "solo")
    assert all(
        attrs.get("type") not in {"scc_cluster", "code_scc_cluster"}
        for _, attrs in acyclic.nodes(data=True)
    )
