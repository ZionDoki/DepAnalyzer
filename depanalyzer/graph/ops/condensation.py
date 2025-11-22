"""Shared utilities for SCC-based cycle elimination views.

The condensed graph (also known as the condensation graph) collapses each
strongly connected component into a single cluster node so that algorithms
that require a DAG can operate on the resulting structure without mutating the
original graph. Each cluster node surfaces the original members so that
results can be mapped back to exact graph IDs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple, Union

import networkx as nx

logger = logging.getLogger("depanalyzer.graph.ops.condensation")

# NetworkX uses MultiDiGraph for transaction graphs and DiGraph for the global
# package view. Both are supported by the condensation pipeline.
GraphLike = Union[nx.DiGraph, nx.MultiDiGraph]


@dataclass(frozen=True)
class CondensationResult:
    """Container describing a condensation DAG.

    Attributes:
        dag: Directed acyclic graph whose nodes represent SCC clusters.
        member_to_cluster: Mapping from original node IDs to the cluster ID
            that contains them. Algorithms can use this to translate results
            back to the unmodified graph without performing new SCC queries.
    """

    dag: nx.DiGraph
    member_to_cluster: Dict[str, str]


def _component_sort_key(component: Sequence[str]) -> Tuple[int, Sequence[str]]:
    """Sorting helper to keep cluster IDs deterministic across runs."""

    return (len(component), component)


def build_condensation_dag(
    graph: GraphLike,
    *,
    node_prefix: str,
    cluster_type: str,
) -> CondensationResult:
    """Collapse strongly connected components into a DAG view.

    Args:
        graph: Source graph (DiGraph or MultiDiGraph).
        node_prefix: Prefix for generated cluster node IDs (e.g. "global_scc:").
        cluster_type: Node type stored in each cluster's attributes.

    Returns:
        CondensationResult describing the condensed DAG and membership mapping.
    """

    cluster_graph = nx.DiGraph()
    membership_lookup: Dict[Any, str] = {}
    membership_export: Dict[str, str] = {}

    if graph.number_of_nodes() == 0:
        # Nothing to cluster; return the empty view.
        return CondensationResult(dag=cluster_graph, member_to_cluster={})

    components: List[Tuple[List[str], List[Any]]] = []

    for component in nx.strongly_connected_components(graph):
        component_nodes = list(component)
        members = sorted(str(node_id) for node_id in component_nodes)
        components.append((members, component_nodes))

    # Deterministic ordering to make cluster IDs stable.
    components.sort(key=lambda item: _component_sort_key(item[0]))

    # Replay SCCs to populate cluster nodes and membership mapping.
    for index, (members, component_nodes) in enumerate(components, start=1):
        cluster_id = f"{node_prefix}{index}"
        cluster_graph.add_node(
            cluster_id,
            type=cluster_type,
            members=members,
            member_count=len(members),
        )
        for node_obj in component_nodes:
            membership_lookup[node_obj] = cluster_id
            membership_export[str(node_obj)] = cluster_id

    # Build edges across clusters by mapping every original edge to its cluster.
    for source, target in graph.edges():
        src_cluster = membership_lookup.get(source)
        dst_cluster = membership_lookup.get(target)
        if not src_cluster or not dst_cluster or src_cluster == dst_cluster:
            continue
        cluster_graph.add_edge(src_cluster, dst_cluster)

    logger.debug(
        "Built condensation DAG: %d clusters, %d edges",
        cluster_graph.number_of_nodes(),
        cluster_graph.number_of_edges(),
    )

    return CondensationResult(dag=cluster_graph, member_to_cluster=membership_export)
