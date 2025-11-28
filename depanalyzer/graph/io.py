"""Shared graph utilities for export-time graph cleanup."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Dict, Iterable, List, Set, Tuple

import networkx as nx

from .models.schema import EdgeKind, NodeType

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
    - remove all original edges among SCC members
    - attach members to the cluster via ``contains`` edges (cluster -> member)

    Note: We do NOT rewire external edges to point to the cluster; inbound/outbound
    edges remain connected to the original members. This preserves the outer
    connectivity while eliminating cycles, and allows analyses that care about
    SCC grouping to follow the contains edges explicitly.

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

            # Rewire external edges to the cluster while preserving metadata so
            # downstream consumers keep edge semantics (e.g., depends_on).
            pred_edges = []
            succ_edges = []
            for member in members:
                try:
                    edge_data = dag.get_edge_data
                except Exception:  # pragma: no cover - defensive
                    edge_data = None

                for pred in dag.predecessors(member):
                    if pred in member_set:
                        continue
                    data_map = edge_data(pred, member) if edge_data else None
                    for attrs in (data_map or {}).values():
                        pred_edges.append((pred, dict(attrs or {})))

                for succ in dag.successors(member):
                    if succ in member_set:
                        continue
                    data_map = edge_data(member, succ) if edge_data else None
                    for attrs in (data_map or {}).values():
                        succ_edges.append((succ, dict(attrs or {})))

            seen_pred = set()
            for pred, attrs in pred_edges:
                try:
                    dedup_token = json.dumps(attrs or {}, sort_keys=True, default=str)
                except Exception:
                    dedup_token = str(attrs)
                dedup_key = (pred, dedup_token)
                if dedup_key in seen_pred:
                    continue
                seen_pred.add(dedup_key)
                dag.add_edge(pred, cluster_id, **attrs)

            seen_succ = set()
            for succ, attrs in succ_edges:
                try:
                    dedup_token = json.dumps(attrs or {}, sort_keys=True, default=str)
                except Exception:
                    dedup_token = str(attrs)
                dedup_key = (succ, dedup_token)
                if dedup_key in seen_succ:
                    continue
                seen_succ.add(dedup_key)
                dag.add_edge(cluster_id, succ, **attrs)

            # Remove all edges among members (including self-loops). Deduplicate
            # to avoid double-counting under platform-specific ordering quirks.
            internal_edges = {
                (u, v, k)
                for u, v, k in dag.subgraph(member_set).edges(keys=True)
            }
            for source, target, key in internal_edges:
                if removed_edges >= max_removals:
                    logger.warning(
                        "Reached max_removals=%d while breaking cycles; graph may "
                        "still contain cycles.",
                        max_removals,
                    )
                    break
                if _remove_edge_with_key(dag, source, target, key):
                    removed_edges += 1

            # Attach members to the virtual cluster (cluster -> member).
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
