"""Graph manager for single transaction.

GraphManager is the unique graph instance within a transaction,
managing all nodes, edges, and metadata for that analysis session.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from graph.backend import GraphBackend

logger = logging.getLogger("depanalyzer.graph.manager")


class NodeType:
    """Node type constants."""

    ASSET = "asset"
    PROCESS = "process"
    TARGET = "target"
    ARTIFACT = "artifact"
    BUILD_CONFIG = "build_config"
    EXTERNAL_DEP = "external_dep"
    TOOLCHAIN = "toolchain"
    PROXY = "proxy"

    # Legacy types for backward compatibility
    CODE = "code"
    HEADER = "header"
    PROJECT_HEADER = "project_header"
    SYSTEM_HEADER = "system_header"
    SHARED_LIBRARY = "shared_library"
    STATIC_LIBRARY = "static_library"
    EXECUTABLE = "executable"
    MODULE = "module"


class EdgeKind:
    """Edge kind constants."""

    CONSUMES = "consumes"
    PRODUCES = "produces"
    DEPENDS_ON = "depends_on"
    LINKS = "links"
    CONTAINS = "contains"
    DECLARES = "declares"

    # Legacy kinds
    SOURCES = "sources"
    INCLUDE = "include"
    IMPORT = "import"
    PART_OF = "part_of"
    AFFECTS = "affects"
    LINK_LIBRARIES = "link_libraries"


class GraphManager:
    """Graph manager for a single transaction.

    Each transaction maintains exactly one GraphManager instance that
    stores all nodes, edges, and metadata for that analysis session.
    """

    def __init__(self, graph_id: Optional[str] = None) -> None:
        """Initialize graph manager.

        Args:
            graph_id: Optional graph identifier.
        """
        self.graph_id = graph_id or "default"
        self._backend = GraphBackend()
        self._metadata: Dict[str, Any] = {}

        logger.info("GraphManager initialized with ID: %s", self.graph_id)

    def add_node(
        self,
        node_id: str,
        node_type: str,
        confidence: float = 1.0,
        over_approx: bool = False,
        evidence: Optional[List[str]] = None,
        **attributes: Any,
    ) -> None:
        """Add node to graph.

        Args:
            node_id: Unique node identifier.
            node_type: Node type (see NodeType constants).
            confidence: Confidence score (0.0-1.0).
            over_approx: Whether this is an over-approximation.
            evidence: List of evidence sources.
            **attributes: Additional node attributes.
        """
        if self._backend.has_node(node_id):
            logger.debug("Node %s already exists, skipping", node_id)
            return

        attrs = {
            "type": node_type,
            "confidence": confidence,
            "over_approx": over_approx,
            **({"evidence": evidence} if evidence else {}),
            **attributes,
        }

        self._backend.add_node(node_id, **attrs)
        logger.debug("Added node: %s (type=%s)", node_id, node_type)

    def add_edge(
        self,
        source: str,
        target: str,
        edge_kind: str,
        confidence: float = 1.0,
        over_approx: bool = False,
        evidence: Optional[List[str]] = None,
        **attributes: Any,
    ) -> int:
        """Add edge to graph.

        Args:
            source: Source node ID.
            target: Target node ID.
            edge_kind: Edge kind (see EdgeKind constants).
            confidence: Confidence score (0.0-1.0).
            over_approx: Whether this is an over-approximation.
            evidence: List of evidence sources.
            **attributes: Additional edge attributes.

        Returns:
            int: Edge key.
        """
        attrs = {
            "kind": edge_kind,
            "confidence": confidence,
            "over_approx": over_approx,
            **({"evidence": evidence} if evidence else {}),
            **attributes,
        }

        key = self._backend.add_edge(source, target, **attrs)
        logger.debug(
            "Added edge: %s -> %s (kind=%s, key=%d)", source, target, edge_kind, key
        )
        return key

    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Get node attributes.

        Args:
            node_id: Node identifier.

        Returns:
            Optional[Dict[str, Any]]: Node attributes or None.
        """
        return self._backend.get_node_data(node_id)

    def has_node(self, node_id: str) -> bool:
        """Check if node exists.

        Args:
            node_id: Node identifier.

        Returns:
            bool: True if node exists.
        """
        return self._backend.has_node(node_id)

    def nodes(self, node_type: Optional[str] = None) -> List[tuple[str, Dict[str, Any]]]:
        """Get all nodes, optionally filtered by type.

        Args:
            node_type: Optional node type filter.

        Returns:
            List[tuple[str, Dict[str, Any]]]: List of (node_id, attributes) tuples.
        """
        nodes = list(self._backend.nodes(data=True))
        if node_type:
            nodes = [(nid, attrs) for nid, attrs in nodes if attrs.get("type") == node_type]
        return nodes

    def edges(
        self, edge_kind: Optional[str] = None
    ) -> List[tuple[str, str, int, Dict[str, Any]]]:
        """Get all edges, optionally filtered by kind.

        Args:
            edge_kind: Optional edge kind filter.

        Returns:
            List[tuple[str, str, int, Dict[str, Any]]]: List of (source, target, key, attributes) tuples.
        """
        edges = list(self._backend.edges(data=True, keys=True))
        if edge_kind:
            edges = [
                (u, v, k, d)
                for u, v, k, d in edges
                if d.get("kind") == edge_kind
            ]
        return edges

    def node_count(self) -> int:
        """Get number of nodes.

        Returns:
            int: Node count.
        """
        return self._backend.node_count()

    def edge_count(self) -> int:
        """Get number of edges.

        Returns:
            int: Edge count.
        """
        return self._backend.edge_count()

    def set_metadata(self, key: str, value: Any) -> None:
        """Set graph metadata.

        Args:
            key: Metadata key.
            value: Metadata value.
        """
        self._metadata[key] = value

    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Get graph metadata.

        Args:
            key: Metadata key.
            default: Default value if key not found.

        Returns:
            Any: Metadata value.
        """
        return self._metadata.get(key, default)

    def get_summary(self) -> Dict[str, Any]:
        """Get graph summary.

        Returns:
            Dict[str, Any]: Summary containing node/edge counts and metadata.
        """
        return {
            "graph_id": self.graph_id,
            "node_count": self.node_count(),
            "edge_count": self.edge_count(),
            "metadata": self._metadata,
        }

    def save(self, file_path: Path, format: str = "json") -> None:
        """Save graph to file.

        Args:
            file_path: Output file path.
            format: Output format ('json' or 'gml').
        """
        import networkx as nx

        file_path = Path(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        if format == "json":
            data = nx.readwrite.json_graph.node_link_data(
                self._backend.native_graph, edges="edges"
            )
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info("Graph saved to: %s", file_path)
        elif format == "gml":
            nx.write_gml(self._backend.native_graph, str(file_path))
            logger.info("Graph saved to: %s", file_path)
        else:
            raise ValueError(f"Unsupported format: {format}")

    @classmethod
    def load(cls, file_path: Path, format: str = "json") -> "GraphManager":
        """Load graph from file.

        Args:
            file_path: Input file path.
            format: Input format ('json' or 'gml').

        Returns:
            GraphManager: Loaded graph manager.
        """
        import networkx as nx

        manager = cls()

        if format == "json":
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            manager._backend._graph = nx.readwrite.json_graph.node_link_graph(
                data, edges="edges"
            )
            logger.info("Graph loaded from: %s", file_path)
        elif format == "gml":
            manager._backend._graph = nx.read_gml(str(file_path))
            logger.info("Graph loaded from: %s", file_path)
        else:
            raise ValueError(f"Unsupported format: {format}")

        return manager
