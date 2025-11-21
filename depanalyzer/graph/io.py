"""Shared graph utilities for export-time graph cleanup."""

from __future__ import annotations

import hashlib
import logging
from typing import Dict, Iterable, List, Set, Tuple

import networkx as nx

from depanalyzer.graph.schema import EdgeKind, NodeType

logger = logging.getLogger("depanalyzer.graph.io")


def _generate_cluster_id(members: List[str], prefix: str = "scc") -> str:
    """Generate a deterministic ID for a cluster of nodes."""
    sorted_members = sorted(str(member) for member in members)
    digest = hashlib.sha256(",".join(sorted_members).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest[:16]}"


def _determine_cluster_type(graph: nx.MultiDiGraph, members: List[str]) -> str:
    """Infer a cluster node type from its members."""
    if not members:
        return NodeType.SCC_CLUSTER.value

    first_member = members[0]
    node_data: Dict = graph.nodes[first_member] if graph.has_node(first_member) else {}
    node_type = node_data.get("type")

    code_like_types = {
        NodeType.CODE.value,
        NodeType.SYSTEM_HEADER.value,
        NodeType.PROJECT_HEADER.value,
    }
    if node_type in code_like_types or str(first_member).startswith("//"):
        return NodeType.CODE_SCC_CLUSTER.value

    return NodeType.SCC_CLUSTER.value


def _remove_edge_with_key(
    graph: nx.MultiDiGraph, source: str, target: str, key: int | str | None
) -> bool:
    """Remove an edge if present, returning True when removed."""
    if graph.is_multigraph():
        if graph.has_edge(source, target, key=key):
            graph.remove_edge(source, target, key=key)
            return True
        return False

    if graph.has_edge(source, target):
        graph.remove_edge(source, target)
        return True
    return False


def break_cycles(
    graph: nx.MultiDiGraph,
    *,
    max_removals: int = 50000,  # Kept for backward compatibility with callers.
) -> Tuple[nx.MultiDiGraph, int]:
    """
    Collapse each strongly connected component into a virtual node.

    For every SCC, we:
    - create a virtual cluster node
    - redirect incoming edges from outside the SCC to the cluster
    - redirect outgoing edges from the SCC to the cluster
    - remove all original edges among SCC members
    - attach members to the cluster via ``contains`` edges

    Args:
        graph: Input graph (MultiDiGraph).
        max_removals: Maximum number of edges to remove before aborting.

    Returns:
        Tuple[nx.MultiDiGraph, int]: (Acyclic graph, number of removed edges).
    """
    dag = graph.copy()

    try:
        sccs: List[Set[str]] = list(nx.strongly_connected_components(dag))
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("Failed to compute SCCs: %s. Returning original graph.", exc)
        return dag, 0

    removed_edges = 0

    try:
        for scc in sccs:
            members = list(scc)

            if len(members) == 1:
                node = members[0]
                if dag.has_edge(node, node):
                    self_edges = list(dag.get_edge_data(node, node, default={}).keys())
                    for key in self_edges:
                        if removed_edges >= max_removals:
                            break
                        if _remove_edge_with_key(dag, node, node, key):
                            removed_edges += 1
                continue

            member_set = set(members)
            cluster_id = _generate_cluster_id(members)
            cluster_type = _determine_cluster_type(dag, members)

            if not dag.has_node(cluster_id):
                dag.add_node(
                    cluster_id,
                    type=cluster_type,
                    members=sorted(str(m) for m in members),
                    label="Cycle Cluster",
                    provisional=True,
                )

            edges_to_add: List[Tuple[str, str, Dict]] = []
            edges_to_remove: List[Tuple[str, str, int | str | None]] = []

            for member in members:
                # Incoming edges from outside the SCC -> cluster
                in_edges = list(dag.in_edges(member, keys=True, data=True))
                for upstream, _, key, data in in_edges:
                    if upstream in member_set:
                        edges_to_remove.append((upstream, member, key))
                        continue
                    payload = dict(data or {})
                    payload["original_target"] = str(member)
                    edges_to_add.append((str(upstream), cluster_id, payload))
                    edges_to_remove.append((upstream, member, key))

                # Outgoing edges to outside the SCC: cluster -> downstream
                out_edges = list(dag.out_edges(member, keys=True, data=True))
                for _, downstream, key, data in out_edges:
                    if downstream in member_set:
                        edges_to_remove.append((member, downstream, key))
                        continue
                    payload = dict(data or {})
                    payload["original_source"] = str(member)
                    edges_to_add.append((cluster_id, str(downstream), payload))
                    edges_to_remove.append((member, downstream, key))

            for source, target, payload in edges_to_add:
                dag.add_edge(source, target, **payload)

            for source, target, key in edges_to_remove:
                if removed_edges >= max_removals:
                    logger.warning(
                        "Reached max_removals=%d while breaking cycles; graph may "
                        "still contain cycles.",
                        max_removals,
                    )
                    break
                if _remove_edge_with_key(dag, source, target, key):
                    removed_edges += 1

            # Attach members to the virtual cluster.
            for member in members:
                dag.add_edge(
                    cluster_id,
                    str(member),
                    kind=EdgeKind.CONTAINS.value,
                    implicit=True,
                )

    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("Cycle removal failed: %s", exc)
        return dag, removed_edges

    return dag, removed_edges
