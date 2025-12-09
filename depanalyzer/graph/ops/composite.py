"""Composite graph view for streaming merge.

This module provides a CompositeGraph class that presents a unified view
over multiple GraphBackend instances without merging them in memory.
"""

import json
from typing import Any, Dict, Iterable, Iterator, List, Tuple

from depanalyzer.graph.core.backend import GraphBackend


class CompositeGraph:
    """Read-only view over multiple graph backends.

    Aggregates nodes and edges from a main graph and multiple dependency graphs.
    Handles namespace prefixing dynamically during iteration.
    """

    def __init__(self, main_graph: GraphBackend, main_graph_id: str):
        """Initialize composite graph.

        Args:
            main_graph: The primary transaction graph backend.
            main_graph_id: ID of the main graph.
        """
        self.main_graph = main_graph
        self.main_graph_id = main_graph_id
        # List of (graph_id, backend, namespace) tuples
        self._dependencies: List[Tuple[str, GraphBackend, str]] = []
        self._metadata: Dict[str, Any] = {}

    def add_dependency(self, graph_id: str, backend: GraphBackend, namespace: str) -> None:
        """Add a dependency graph to the composite view.

        Args:
            graph_id: ID of the dependency graph.
            backend: Backend instance for the dependency graph.
            namespace: Namespace prefix for nodes in this graph (e.g., "dep:libfoo:").
        """
        self._dependencies.append((graph_id, backend, namespace))

    def set_metadata(self, metadata: Dict[str, Any]) -> None:
        """Set top-level metadata for the composite graph."""
        self._metadata = metadata

    def nodes(self, data: bool = False) -> Iterator[Any]:
        """Iterate over all nodes in the composite graph."""
        # 1. Yield main graph nodes (no prefix)
        for node_item in self.main_graph.nodes(data=data):
            if data:
                nid, attrs = node_item
                attrs = dict(attrs or {})
                # Enrich attributes if needed
                attrs["graph_id"] = self.main_graph_id
                yield nid, attrs
            else:
                yield node_item

        # 2. Yield dependency graph nodes (prefixed)
        for graph_id, backend, namespace in self._dependencies:
            for node_item in backend.nodes(data=data):
                if data:
                    nid, attrs = node_item
                    attrs = dict(attrs or {})
                    prefixed_id = f"{namespace}{nid}"
                    attrs["graph_id"] = graph_id
                    attrs["graph_namespace"] = namespace
                    attrs["orig_id"] = nid
                    attrs["label"] = prefixed_id  # Sanitize if needed
                    yield prefixed_id, attrs
                else:
                    prefixed_id = f"{namespace}{node_item}"
                    yield prefixed_id

    def edges(self, data: bool = False) -> Iterator[Any]:
        """Iterate over all edges in the composite graph."""
        # 1. Yield main graph edges
        for edge_item in self.main_graph.edges(data=data, keys=False):
            # edge_item is (u, v) or (u, v, d)
            yield edge_item

        # 2. Yield dependency graph edges (prefixed)
        for graph_id, backend, namespace in self._dependencies:
            for edge_item in backend.edges(data=data, keys=False):
                u, v = edge_item[0], edge_item[1]
                prefixed_u = f"{namespace}{u}"
                prefixed_v = f"{namespace}{v}"
                
                if data:
                    yield prefixed_u, prefixed_v, edge_item[2]
                else:
                    yield prefixed_u, prefixed_v

        # 3. Yield cross-graph edges
        # This requires finding proxy nodes in main graph and linking to dependency roots
        # For a truly streaming approach, we might pre-calculate these or generate on-the-fly
        # if we index the roots.
        # See merge.py logic for implementation details.
        
        # Note: Actual cross-edge generation logic belongs in the merge/export function
        # utilizing this class, or this class needs an add_cross_edge method.
        # For simplicity, we can yield explicitly added cross-edges.
        
    def number_of_nodes(self) -> int:
        """Total node count."""
        count = self.main_graph.node_count()
        for _, backend, _ in self._dependencies:
            count += backend.node_count()
        return count

    def number_of_edges(self) -> int:
        """Total edge count."""
        count = self.main_graph.edge_count()
        for _, backend, _ in self._dependencies:
            count += backend.edge_count()
        return count
