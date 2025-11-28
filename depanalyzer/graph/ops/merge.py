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

from ..id_utils import (
    apply_namespace_prefix,
    derive_dependency_namespace,
    ensure_namespace_unique,
)
from ..io import break_cycles
from ..models.linking import LinkClass
from ..models.schema import EdgeKind
from ..registry import GraphRegistry
from .global_dag import GlobalDAG

logger = logging.getLogger("depanalyzer.graph.ops.merge")


def _load_graph_from_cache(cache_path: Path) -> Tuple[nx.MultiDiGraph, Dict]:
    """Load a cached graph from disk.

    Args:
        cache_path: Path to cached graph JSON file.

    Returns:
        Tuple of (nx.MultiDiGraph, metadata dict).
    """
    with cache_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    graph_section = data.get("graph")
    if isinstance(graph_section, dict) and graph_section.get("nodes"):
        graph_data = graph_section
    else:
        graph_data = data
    if not isinstance(graph_data, dict):
        raise ValueError(f"Unsupported graph format in cache: {cache_path}")
    metadata = data.get("metadata") or {}

    edge_key = "edges" if "edges" in graph_data else "links"
    graph_data.setdefault(edge_key, [])
    g = node_link_graph(graph_data, edges=edge_key)
    return g, metadata


def _compute_dead_nodes_for_graph(graph: nx.MultiDiGraph) -> List[str]:
    """Compute unreachable nodes for a merged graph.

    Traverses forward along all edges and also allows reachability to flow
    from members back to their containers via ``contains`` edges to account
    for SCC clusters and other grouping nodes.
    """

    def _has_contains_edge(u: str, v: str) -> bool:
        edge_data = graph.get_edge_data(u, v) or {}
        return any((attrs or {}).get("kind") == EdgeKind.CONTAINS.value for attrs in edge_data.values())

    roots: List[str] = [
        node_id
        for node_id, attrs in graph.nodes(data=True)
        if (attrs or {}).get("type") in {"hap", "har", "hsp", "executable"}
    ]

    reachable: Set[str] = set()
    stack: List[str] = list(roots)

    while stack:
        current = stack.pop()
        if current in reachable:
            continue

        reachable.add(current)

        for succ in graph.successors(current):
            if succ not in reachable:
                stack.append(succ)

        for pred in graph.predecessors(current):
            if pred in reachable:
                continue
            if _has_contains_edge(pred, current):
                stack.append(pred)

    all_nodes: Set[str] = set(graph.nodes())
    return sorted(all_nodes - reachable)


def _filter_dead_nodes_to_main_graph(
    dead_nodes: List[str], graph: nx.MultiDiGraph, main_graph_id: str
) -> List[str]:
    """Keep only dead nodes that belong to the main graph namespace."""
    filtered: List[str] = []
    for nid in dead_nodes:
        try:
            attrs = graph.nodes[nid]
        except Exception:
            continue
        if attrs.get("graph_id") == main_graph_id:
            filtered.append(nid)
    return sorted(filtered)


def merge_graph_with_dependencies(
    main_graph_id: str,
    cache_root: Path | None = None,
) -> Dict:
    """Merge main graph with all transitive dependency graphs.

    The merged graph:
    - Contains all nodes/edges from the main graph (unmodified IDs).
    - Contains all nodes/edges from each dependency graph, with node IDs
      prefixed by `dep:<namespace>:` to avoid collisions while keeping
      identifiers human-meaningful.
    - Adds cross-graph DEPENDS_ON edges from proxy nodes in the main
      graph to all in-degree-zero nodes in the corresponding dependency
      graphs.

    Args:
        main_graph_id: Graph ID of the main transaction.
        cache_root: Root directory for graph cache files.

    Returns:
        Dict: Node-link dictionary (with both ``edges`` and ``links`` keys)
        containing the merged graph plus top-level metadata suitable for
        direct export.
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
    namespace_assignments: Dict[str, str] = {}

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
        namespace: str | None,
        is_dependency: bool,
    ) -> None:
        for node_id, attrs in graph.nodes(data=True):
            new_id = apply_namespace_prefix(str(node_id), namespace, is_dependency)
            new_attrs = dict(attrs or {})
            new_attrs.setdefault("graph_id", graph_id)
            new_attrs.setdefault("graph_namespace", namespace)
            new_attrs.setdefault("orig_id", str(node_id))
            merged.add_node(new_id, **new_attrs)

        for u, v, _key, attrs in graph.edges(data=True, keys=True):
            new_u = apply_namespace_prefix(str(u), namespace, is_dependency)
            new_v = apply_namespace_prefix(str(v), namespace, is_dependency)
            new_attrs = dict(attrs or {})
            merged.add_edge(new_u, new_v, **new_attrs)

    # 1) Copy main graph as-is (no prefixing)
    registry_entry = registry.get_entry(main_graph_id) or {}
    main_summary = registry_entry.get("summary") or {}
    main_namespace = ensure_namespace_unique(
        derive_dependency_namespace(
            main_graph_id,
            main_meta,
            main_summary,
        ),
        main_graph_id,
        namespace_assignments,
    )

    _copy_graph(main_graph, main_graph_id, namespace=main_namespace, is_dependency=False)

    # 2) Copy each dependency graph with a unique prefix
    dep_namespaces: Dict[str, str] = {}

    for dep_id in sorted(dep_ids):
        dep_cache = registry.get_cache_path(dep_id)
        if not dep_cache:
            logger.warning(
                "Dependency graph %s not found in registry, skipping during merge",
                dep_id,
            )
            continue

        dep_entry = registry.get_entry(dep_id) or {}
        dep_summary = dep_entry.get("summary") or {}

        dep_graph, dep_meta = _load_graph_from_cache(dep_cache)

        # Compute in-degree-zero nodes in the original dependency graph.
        roots: List[str] = [
            n for n in dep_graph.nodes() if dep_graph.in_degree(n) == 0
        ]
        child_root_nodes[dep_id] = roots

        dep_namespace = ensure_namespace_unique(
            derive_dependency_namespace(dep_id, dep_meta, dep_summary),
            dep_id,
            namespace_assignments,
        )
        dep_namespaces[dep_id] = dep_namespace

        _copy_graph(dep_graph, dep_id, namespace=dep_namespace, is_dependency=True)

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

        if child_id not in dep_namespaces:
            continue

        roots = child_root_nodes[child_id]

        for root in roots:
            root_id = apply_namespace_prefix(str(root), dep_namespaces.get(child_id), True)
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

    # Recompute dead nodes on the full merged graph (before cycle breaking)
    # to ensure reachability analysis isn't affected by removed edges.
    main_meta = dict(main_meta or {})
    try:
        recomputed_dead = _compute_dead_nodes_for_graph(merged)
        filtered_dead = _filter_dead_nodes_to_main_graph(
            recomputed_dead, merged, main_graph_id
        )
        if filtered_dead:
            main_meta["dead_nodes"] = filtered_dead
        else:
            main_meta.pop("dead_nodes", None)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.debug("Failed to recompute dead nodes for merged graph: %s", exc)

    # Remove cycles before exporting to keep downstream processing fast.
    acyclic_graph, removed_edges = break_cycles(merged)

    merged_metadata = {
        "graph_id": main_graph_id,
        "merged": True,
        "merged_from": [main_graph_id] + sorted(dep_ids),
        "node_count": acyclic_graph.number_of_nodes(),
        "edge_count": acyclic_graph.number_of_edges(),
        "main_metadata": main_meta,
        "graph_namespaces": {main_graph_id: main_namespace, **dep_namespaces},
    }
    if removed_edges:
        merged_metadata["sanitization"] = {"cycle_edges_removed": int(removed_edges)}

    # Serialize to node-link format
    # Carry metadata as graph attributes so node_link_data keeps them
    acyclic_graph.graph.clear()
    acyclic_graph.graph.update(merged_metadata)

    merged_graph_data = node_link_data(acyclic_graph, edges="edges")

    merged_graph_data["metadata"] = merged_metadata
    merged_graph_data["graph_id"] = main_graph_id
    merged_graph_data["source"] = main_meta.get("source")

    return merged_graph_data
