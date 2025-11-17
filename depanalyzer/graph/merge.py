"""Utilities for merging transaction graphs and their dependencies.

The primary entry point is `merge_graph_with_dependencies`, which
combines the main transaction graph with all transitive dependency
graphs tracked in GlobalDAG into a single NetworkX MultiDiGraph and
returns it in node-link (JSON-serializable) form.

Cross-graph wiring is derived from:
- GlobalDAG edges (parent_graph_id -> child_graph_id)
- Proxy nodes in the parent graph with `child_graph_id` metadata
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Set, Tuple

import networkx as nx
from networkx.readwrite import node_link_data, node_link_graph

from depanalyzer.graph.global_dag import GlobalDAG
from depanalyzer.graph.linking import LinkClass
from depanalyzer.graph.manager import EdgeKind
from depanalyzer.graph.registry import GraphRegistry

logger = logging.getLogger("depanalyzer.graph.merge")


def _load_graph_from_cache(cache_path: Path) -> Tuple[nx.MultiDiGraph, Dict]:
    """Load a cached graph from disk.

    Args:
        cache_path: Path to cached graph JSON file.

    Returns:
        Tuple of (nx.MultiDiGraph, metadata dict).
    """
    with cache_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    graph_data = data.get("graph") or data
    metadata = data.get("metadata") or {}

    g = node_link_graph(graph_data, edges="links")
    return g, metadata


def merge_graph_with_dependencies(
    main_graph_id: str,
    cache_root: Path | None = None,
) -> Dict:
    """Merge main graph with all transitive dependency graphs.

    The merged graph:
    - Contains all nodes/edges from the main graph (unmodified IDs).
    - Contains all nodes/edges from each dependency graph, with node IDs
      prefixed by `dep:<graph_id>:` to avoid collisions.
    - Adds cross-graph DEPENDS_ON edges from proxy nodes in the main
      graph to all in-degree-zero nodes in the corresponding dependency
      graphs.

    Args:
        main_graph_id: Graph ID of the main transaction.
        cache_root: Root directory for graph cache files.

    Returns:
        Dict: JSON-serializable dict with structure:
            {
                "metadata": {...},
                "graph": <node_link_data>,
            }
    """
    cache_root = cache_root or (Path(".depanalyzer_cache") / "graphs")
    registry = GraphRegistry.get_instance(cache_root=cache_root)

    main_cache = registry.get_cache_path(main_graph_id)
    if not main_cache:
        raise ValueError(f"Main graph not found in registry: {main_graph_id}")

    logger.info(
        "Merging main graph %s with dependencies (cache_root=%s)",
        main_graph_id,
        cache_root,
    )

    main_graph, main_meta = _load_graph_from_cache(main_cache)

    # Discover all transitive dependency graph IDs
    global_dag = GlobalDAG.get_instance(cache_dir=cache_root)
    dep_ids: Set[str] = global_dag.get_transitive_dependencies(main_graph_id)

    logger.info(
        "Found %d transitive dependency graphs for %s: %s",
        len(dep_ids),
        main_graph_id,
        ", ".join(sorted(dep_ids)) if dep_ids else "(none)",
    )

    merged = nx.MultiDiGraph()

    # Track in-degree-zero nodes for each dependency graph (original IDs)
    child_root_nodes: Dict[str, List[str]] = {}

    # Helper to copy nodes/edges from a graph into merged graph with optional prefix.
    def _copy_graph(
        graph: nx.MultiDiGraph,
        graph_id: str,
        prefix: str | None,
    ) -> None:
        for node_id, attrs in graph.nodes(data=True):
            new_id = f"{prefix}{node_id}" if prefix else node_id
            new_attrs = dict(attrs or {})
            new_attrs.setdefault("graph_id", graph_id)
            new_attrs.setdefault("orig_id", str(node_id))
            merged.add_node(new_id, **new_attrs)

        for u, v, key, attrs in graph.edges(data=True, keys=True):
            new_u = f"{prefix}{u}" if prefix else u
            new_v = f"{prefix}{v}" if prefix else v
            new_attrs = dict(attrs or {})
            merged.add_edge(new_u, new_v, **new_attrs)

    # 1) Copy main graph as-is (no prefixing)
    _copy_graph(main_graph, main_graph_id, prefix=None)

    # 2) Copy each dependency graph with a unique prefix
    for dep_id in sorted(dep_ids):
        dep_cache = registry.get_cache_path(dep_id)
        if not dep_cache:
            logger.warning(
                "Dependency graph %s not found in registry, skipping during merge",
                dep_id,
            )
            continue

        dep_graph, _ = _load_graph_from_cache(dep_cache)

        # Compute in-degree-zero nodes in the original dependency graph.
        roots: List[str] = [
            n for n in dep_graph.nodes() if dep_graph.in_degree(n) == 0
        ]
        child_root_nodes[dep_id] = roots

        prefix = f"dep:{dep_id}:"
        _copy_graph(dep_graph, dep_id, prefix=prefix)

    # 3) Add cross-graph edges from proxy nodes in the main graph to
    #    roots of the corresponding dependency graphs.
    proxy_nodes: List[Tuple[str, Dict]] = []
    for node_id, attrs in merged.nodes(data=True):
        if attrs.get("type") == "proxy" and attrs.get("child_graph_id"):
            proxy_nodes.append((node_id, attrs))

    added_cross_edges = 0

    for proxy_id, attrs in proxy_nodes:
        child_id = attrs.get("child_graph_id")
        if not child_id or child_id not in child_root_nodes:
            continue

        roots = child_root_nodes[child_id]
        prefix = f"dep:{child_id}:"

        for root in roots:
            root_id = f"{prefix}{root}"
            if not merged.has_node(root_id):
                continue

            merged.add_edge(
                proxy_id,
                root_id,
                kind=EdgeKind.DEPENDS_ON.value,
                parser_name="graph_merge",
                link_class=LinkClass.CROSS_GRAPH.value,
                derived_from="proxy_child_graph",
            )
            added_cross_edges += 1

    logger.info(
        "Merged graph has %d nodes, %d edges (%d cross-graph edges added)",
        merged.number_of_nodes(),
        merged.number_of_edges(),
        added_cross_edges,
    )

    # Serialize to node-link format
    merged_graph_data = node_link_data(merged, edges="links")

    merged_metadata = {
        "graph_id": main_graph_id,
        "merged": True,
        "merged_from": [main_graph_id] + sorted(dep_ids),
        "node_count": merged.number_of_nodes(),
        "edge_count": merged.number_of_edges(),
        "main_metadata": main_meta,
    }

    return {"metadata": merged_metadata, "graph": merged_graph_data}

