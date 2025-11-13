"""Graph backend abstraction layer.

Wraps NetworkX for easy backend replacement in the future.
"""

import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

import networkx as nx

logger = logging.getLogger("depanalyzer.graph.backend")


class GraphBackend:
    """Abstract graph backend wrapping NetworkX.

    This provides a stable interface that can be swapped for other
    graph libraries in the future without changing the rest of the codebase.
    """

    def __init__(self) -> None:
        """Initialize backend with NetworkX MultiDiGraph."""
        self._graph = nx.MultiDiGraph()
        logger.debug("Graph backend initialized with NetworkX")

    @property
    def native_graph(self) -> nx.MultiDiGraph:
        """Get native NetworkX graph for advanced operations.

        Returns:
            nx.MultiDiGraph: Native graph instance.
        """
        return self._graph

    def add_node(self, node_id: str, **attributes: Any) -> None:
        """Add node to graph.

        Args:
            node_id: Node identifier.
            **attributes: Node attributes.
        """
        self._graph.add_node(node_id, **attributes)

    def add_edge(
        self, source: str, target: str, **attributes: Any
    ) -> int:
        """Add edge to graph.

        Args:
            source: Source node ID.
            target: Target node ID.
            **attributes: Edge attributes.

        Returns:
            int: Edge key.
        """
        return self._graph.add_edge(source, target, **attributes)

    def has_node(self, node_id: str) -> bool:
        """Check if node exists.

        Args:
            node_id: Node identifier.

        Returns:
            bool: True if node exists.
        """
        return self._graph.has_node(node_id)

    def has_edge(self, source: str, target: str, key: Optional[int] = None) -> bool:
        """Check if edge exists.

        Args:
            source: Source node ID.
            target: Target node ID.
            key: Optional edge key.

        Returns:
            bool: True if edge exists.
        """
        if key is None:
            return self._graph.has_edge(source, target)
        return self._graph.has_edge(source, target) and key in self._graph[source][target]

    def get_node_data(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Get node attributes.

        Args:
            node_id: Node identifier.

        Returns:
            Optional[Dict[str, Any]]: Node attributes or None if not found.
        """
        return self._graph.nodes.get(node_id)

    def get_edge_data(
        self, source: str, target: str, key: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """Get edge attributes.

        Args:
            source: Source node ID.
            target: Target node ID.
            key: Optional edge key.

        Returns:
            Optional[Dict[str, Any]]: Edge attributes or None if not found.
        """
        return self._graph.get_edge_data(source, target, key)

    def nodes(self, data: bool = False) -> Iterable:
        """Iterate over nodes.

        Args:
            data: If True, return (node_id, attributes) tuples.

        Returns:
            Iterable: Node iterator.
        """
        return self._graph.nodes(data=data)

    def edges(self, data: bool = False, keys: bool = False) -> Iterable:
        """Iterate over edges.

        Args:
            data: If True, include edge attributes.
            keys: If True, include edge keys.

        Returns:
            Iterable: Edge iterator.
        """
        return self._graph.edges(data=data, keys=keys)

    def node_count(self) -> int:
        """Get number of nodes.

        Returns:
            int: Node count.
        """
        return self._graph.number_of_nodes()

    def edge_count(self) -> int:
        """Get number of edges.

        Returns:
            int: Edge count.
        """
        return self._graph.number_of_edges()

    def successors(self, node_id: str) -> Iterable[str]:
        """Get successor nodes.

        Args:
            node_id: Node identifier.

        Returns:
            Iterable[str]: Successor node IDs.
        """
        return self._graph.successors(node_id)

    def predecessors(self, node_id: str) -> Iterable[str]:
        """Get predecessor nodes.

        Args:
            node_id: Node identifier.

        Returns:
            Iterable[str]: Predecessor node IDs.
        """
        return self._graph.predecessors(node_id)

    def in_degree(self, node_id: str) -> int:
        """Get in-degree of node.

        Args:
            node_id: Node identifier.

        Returns:
            int: In-degree.
        """
        return self._graph.in_degree(node_id)

    def out_degree(self, node_id: str) -> int:
        """Get out-degree of node.

        Args:
            node_id: Node identifier.

        Returns:
            int: Out-degree.
        """
        return self._graph.out_degree(node_id)

    def clear(self) -> None:
        """Clear all nodes and edges."""
        self._graph.clear()
        logger.debug("Graph backend cleared")
