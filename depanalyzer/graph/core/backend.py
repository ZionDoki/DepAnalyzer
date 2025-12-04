"""Graph backend abstraction layer.

Wraps NetworkX for easy backend replacement in the future.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, Optional

import networkx as nx

logger = logging.getLogger("depanalyzer.graph.core.backend")


class GraphBackend(ABC):
    """Abstract graph backend protocol.

    This defines the interface that all graph storage backends must implement.
    Implementations can be in-memory (NetworkX), disk-based (SQLite), or remote.
    """

    @property
    @abstractmethod
    def native_graph(self) -> Any:
        """Get native graph object for advanced operations."""
        pass

    @abstractmethod
    def set_native_graph(self, graph: Any) -> None:
        """Replace the underlying graph instance."""
        pass

    @abstractmethod
    def add_node(self, node_id: str, **attributes: Any) -> None:
        """Add node to graph."""
        pass

    @abstractmethod
    def add_edge(self, source: str, target: str, **attributes: Any) -> int:
        """Add edge to graph."""
        pass

    @abstractmethod
    def has_node(self, node_id: str) -> bool:
        """Check if node exists."""
        pass

    @abstractmethod
    def has_edge(self, source: str, target: str, key: Optional[int] = None) -> bool:
        """Check if edge exists."""
        pass

    @abstractmethod
    def get_node_data(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Get node attributes."""
        pass

    @abstractmethod
    def get_edge_data(
        self, source: str, target: str, key: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """Get edge attributes."""
        pass

    @abstractmethod
    def nodes(self, data: bool = False) -> Iterable:
        """Iterate over nodes."""
        pass

    @abstractmethod
    def edges(self, data: bool = False, keys: bool = False) -> Iterable:
        """Iterate over edges."""
        pass

    @abstractmethod
    def node_count(self) -> int:
        """Get number of nodes."""
        pass

    @abstractmethod
    def edge_count(self) -> int:
        """Get number of edges."""
        pass

    @abstractmethod
    def successors(self, node_id: str) -> Iterable[str]:
        """Get successor nodes."""
        pass

    @abstractmethod
    def predecessors(self, node_id: str) -> Iterable[str]:
        """Get predecessor nodes."""
        pass

    @abstractmethod
    def in_degree(self, node_id: str) -> int:
        """Get in-degree of node."""
        pass

    @abstractmethod
    def out_degree(self, node_id: str) -> int:
        """Get out-degree of node."""
        pass

    @abstractmethod
    def clear(self) -> None:
        """Clear all nodes and edges."""
        pass


class NetworkXBackend(GraphBackend):
    """NetworkX-based in-memory graph backend.
    
    This is the default implementation used for active analysis.
    """

    def __init__(self) -> None:
        """Initialize backend with NetworkX MultiDiGraph."""
        self._graph = nx.MultiDiGraph()
        logger.debug("NetworkXBackend initialized")

    @property
    def native_graph(self) -> nx.MultiDiGraph:
        return self._graph

    def set_native_graph(self, graph: nx.MultiDiGraph) -> None:
        self._graph = graph
        logger.debug("Graph backend replaced with provided graph")

    def add_node(self, node_id: str, **attributes: Any) -> None:
        self._graph.add_node(node_id, **attributes)

    def add_edge(
        self, source: str, target: str, **attributes: Any
    ) -> int:
        return self._graph.add_edge(source, target, **attributes)

    def has_node(self, node_id: str) -> bool:
        return self._graph.has_node(node_id)

    def has_edge(self, source: str, target: str, key: Optional[int] = None) -> bool:
        if key is None:
            return self._graph.has_edge(source, target)
        return self._graph.has_edge(source, target) and key in self._graph[source][target]

    def get_node_data(self, node_id: str) -> Optional[Dict[str, Any]]:
        return self._graph.nodes.get(node_id)

    def get_edge_data(
        self, source: str, target: str, key: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        return self._graph.get_edge_data(source, target, key)

    def nodes(self, data: bool = False) -> Iterable:
        return self._graph.nodes(data=data)

    def edges(self, data: bool = False, keys: bool = False) -> Iterable:
        return self._graph.edges(data=data, keys=keys)

    def node_count(self) -> int:
        return self._graph.number_of_nodes()

    def edge_count(self) -> int:
        return self._graph.number_of_edges()

    def successors(self, node_id: str) -> Iterable[str]:
        return self._graph.successors(node_id)

    def predecessors(self, node_id: str) -> Iterable[str]:
        return self._graph.predecessors(node_id)

    def in_degree(self, node_id: str) -> int:
        return self._graph.in_degree(node_id)

    def out_degree(self, node_id: str) -> int:
        return self._graph.out_degree(node_id)

    def clear(self) -> None:
        self._graph.clear()
        logger.debug("Graph backend cleared")