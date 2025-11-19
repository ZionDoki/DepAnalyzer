"""Graph manager for single transaction.

GraphManager is the unique graph instance within a transaction,
managing all nodes, edges, and metadata for that analysis session.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

import networkx as nx

from depanalyzer.graph.backend import GraphBackend
from depanalyzer.graph.condensation import (
    CondensationResult,
    build_condensation_dag,
)
from depanalyzer.graph.projection import (
    derive_asset_artifact_projection as _derive_projection,
    fuse_projection_evidence as _fuse_projection,
)
from depanalyzer.graph.projection_config import ProjectionConfig
from depanalyzer.graph.schema import (
    EdgeKind,
    EdgeSpec,
    NodeSpec,
    NodeType,
    validate_edge,
    validate_node,
)
from depanalyzer.utils.path_utils import normalize_node_id

logger = logging.getLogger("depanalyzer.graph.manager")


class GraphManager:
    """Graph manager for a single transaction.

    Each transaction maintains exactly one GraphManager instance that
    stores all nodes, edges, and metadata for that analysis session.
    """

    def __init__(
        self,
        graph_id: Optional[str] = None,
        root_path: Optional[Union[str, Path]] = None,
        path_namespace: Optional[str] = None,
    ) -> None:
        """Initialize graph manager.

        Args:
            graph_id: Optional graph identifier.
            root_path: Optional root path for node ID normalization.
        """
        self.graph_id = graph_id or "default"
        self.root_path = Path(root_path).resolve() if root_path else None

        self.path_namespace = path_namespace
        self._backend = GraphBackend()
        self._metadata: Dict[str, Any] = {}

        logger.info("GraphManager initialized with ID: %s", self.graph_id)
        if self.root_path:
            logger.info("Root path set to: %s", self.root_path)

    @property
    def backend(self) -> GraphBackend:
        """Return the underlying graph backend.

        Exposing the backend is useful for analysis utilities that need to
        inspect low-level graph structures (e.g., reachability checks) while
        keeping the attribute access explicit instead of touching _backend.
        """
        return self._backend

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

        try:
            ntype = NodeType(node_type)
        except ValueError:
            logger.debug(
                "Unknown node type %s for %s, using UNKNOWN", node_type, node_id
            )
            ntype = NodeType.UNKNOWN

        attrs = dict(attributes)
        if evidence:
            attrs.setdefault("evidence", evidence)

        spec = NodeSpec(
            id=node_id,
            type=ntype,
            confidence=confidence,
            over_approx=over_approx,
            label=attrs.pop("label", None),
            src_path=attrs.get("src_path") or attrs.get("path"),
            path=attrs.get("path"),
            name=attrs.get("name"),
            parser_name=attrs.get("parser_name"),
            attrs=attrs,
        )
        self.add_node_spec(spec)

    def add_node_spec(self, spec: NodeSpec) -> None:
        """Add a node described by a NodeSpec.

        This is the preferred entry point for new code constructing nodes.
        """
        if self._backend.has_node(spec.id):
            logger.debug("Node %s already exists, skipping", spec.id)
            return

        validate_node(spec, self.root_path)
        self._backend.add_node(spec.id, **spec.to_backend_attrs())
        logger.debug("Added node: %s (type=%s)", spec.id, spec.type.value)

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
        try:
            kind = EdgeKind(edge_kind)
        except ValueError:
            logger.debug(
                "Unknown edge kind %s for %s -> %s, defaulting to depends_on",
                edge_kind,
                source,
                target,
            )
            kind = EdgeKind.DEPENDS_ON

        attrs = dict(attributes)
        if evidence:
            attrs.setdefault("evidence", evidence)

        spec = EdgeSpec(
            source=source,
            target=target,
            kind=kind,
            confidence=confidence,
            over_approx=over_approx,
            parser_name=attrs.get("parser_name"),
            attrs=attrs,
        )
        return self.add_edge_spec(spec)

    def add_edge_spec(self, spec: EdgeSpec) -> int:
        """Add an edge described by an EdgeSpec.

        This is the preferred entry point for new code constructing edges.
        """
        if not self._backend.has_node(spec.source):
            provisional_node = NodeSpec(
                id=spec.source,
                type=NodeType.UNKNOWN,
                provisional=True,
                attrs={},
            )
            validate_node(provisional_node, self.root_path)
            self._backend.add_node(spec.source, **provisional_node.to_backend_attrs())

        if not self._backend.has_node(spec.target):
            provisional_node = NodeSpec(
                id=spec.target,
                type=NodeType.UNKNOWN,
                provisional=True,
                attrs={},
            )
            validate_node(provisional_node, self.root_path)
            self._backend.add_node(spec.target, **provisional_node.to_backend_attrs())

        validate_edge(spec)
        key = self._backend.add_edge(
            spec.source,
            spec.target,
            **spec.to_backend_attrs(),
        )
        logger.debug(
            "Added edge: %s -> %s (kind=%s, key=%d)",
            spec.source,
            spec.target,
            spec.kind.value,
            key,
        )
        return key

    def normalize_path(self, path: Union[str, Path]) -> str:
        """
        Normalize a file path to standardized node ID format.

        All node IDs use normalized format:
        - Internal paths: //relative/to/root
        - External paths: //../external/path

        Args:
            path: File path to normalize (absolute or relative)

        Returns:
            Normalized node ID string

        Raises:
            ValueError: If root_path is not set on this GraphManager
        """
        if not self.root_path:
            raise ValueError(
                "root_path must be set on GraphManager to use path normalization. "
                "Initialize GraphManager with root_path parameter."
            )
        return normalize_node_id(path, self.root_path, namespace=self.path_namespace)

    def add_normalized_node(
        self,
        path: Union[str, Path],
        node_type: str,
        confidence: float = 1.0,
        over_approx: bool = False,
        evidence: Optional[List[str]] = None,
        **attributes: Any,
    ) -> str:
        """Add node with automatic path normalization.

        This is a convenience method that normalizes the path before adding
        the node. Useful for parsers working with file paths.

        Args:
            path: File path (will be normalized to node ID)
            node_type: Node type (see NodeType constants)
            confidence: Confidence score (0.0-1.0)
            over_approx: Whether this is an over-approximation
            evidence: List of evidence sources
            **attributes: Additional node attributes

        Returns:
            Normalized node ID

        Raises:
            ValueError: If root_path is not set on this GraphManager
        """
        node_id = self.normalize_path(path)
        self.add_node(
            node_id, node_type, confidence, over_approx, evidence, **attributes
        )
        return node_id

    def add_normalized_edge(
        self,
        source_path: Union[str, Path],
        target_path: Union[str, Path],
        edge_kind: str,
        confidence: float = 1.0,
        over_approx: bool = False,
        evidence: Optional[List[str]] = None,
        **attributes: Any,
    ) -> tuple[str, str, int]:
        """Add edge with automatic path normalization.

        This is a convenience method that normalizes both paths before adding
        the edge. Useful for parsers working with file paths.

        Args:
            source_path: Source file path (will be normalized)
            target_path: Target file path (will be normalized)
            edge_kind: Edge kind (see EdgeKind constants)
            confidence: Confidence score (0.0-1.0)
            over_approx: Whether this is an over-approximation
            evidence: List of evidence sources
            **attributes: Additional edge attributes

        Returns:
            Tuple of (source_id, target_id, edge_key)

        Raises:
            ValueError: If root_path is not set on this GraphManager
        """
        source_id = self.normalize_path(source_path)
        target_id = self.normalize_path(target_path)
        key = self.add_edge(
            source_id,
            target_id,
            edge_kind,
            confidence,
            over_approx,
            evidence,
            **attributes,
        )
        return (source_id, target_id, key)

    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Get node attributes.

        Args:
            node_id: Node identifier.

        Returns:
            Optional[Dict[str, Any]]: Node attributes or None.
        """
        return self._backend.get_node_data(node_id)

    def get_node_attributes(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Get node attributes (alias for get_node).

        Args:
            node_id: Node identifier.

        Returns:
            Optional[Dict[str, Any]]: Node attributes or None.
        """
        return self.get_node(node_id)

    def has_node(self, node_id: str) -> bool:
        """Check if node exists.

        Args:
            node_id: Node identifier.

        Returns:
            bool: True if node exists.
        """
        return self._backend.has_node(node_id)

    def update_node_attribute(self, node_id: str, key: str, value: Any) -> None:
        """Update a single node attribute.

        Args:
            node_id: Node identifier.
            key: Attribute key to update.
            value: New value for the attribute.

        Raises:
            ValueError: If node does not exist.
        """
        if not self.has_node(node_id):
            raise ValueError(f"Node {node_id} does not exist")
        self._backend.native_graph.nodes[node_id][key] = value
        logger.debug("Updated node %s attribute: %s", node_id, key)

    def nodes(
        self, node_type: Optional[str] = None
    ) -> List[tuple[str, Dict[str, Any]]]:
        """Get all nodes, optionally filtered by type.

        Args:
            node_type: Optional node type filter.

        Returns:
            List[tuple[str, Dict[str, Any]]]: List of (node_id, attributes) tuples.
        """
        nodes = list(self._backend.nodes(data=True))
        if node_type:
            nodes = [
                (nid, attrs) for nid, attrs in nodes if attrs.get("type") == node_type
            ]
        return nodes

    def edges(
        self, edge_kind: Optional[str] = None
    ) -> List[tuple[str, str, int, Dict[str, Any]]]:
        """Get all edges, optionally filtered by kind.

        Args:
            edge_kind: Optional edge kind filter.

        Returns:
            List[tuple[str, str, int, Dict[str, Any]]]: (source, target, key, attributes) tuples.
        """
        edges = list(self._backend.edges(data=True, keys=True))
        if edge_kind:
            edges = [(u, v, k, d) for u, v, k, d in edges if d.get("kind") == edge_kind]
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

    def build_code_cluster_view(self) -> CondensationResult:
        """Build a condensation DAG for the current transaction graph.

        Code-level dependency graphs often contain cycles (mutual imports,
        recursive calls, etc.). Algorithms that require a DAG should rely on
        this method to obtain a compact `code_scc_cluster` view instead of
        mutating the original graph.

        Returns:
            CondensationResult: DAG composed of code SCC clusters.
        """

        prefix = f"code_scc:{self.graph_id}:"
        return build_condensation_dag(
            self._backend.native_graph,
            node_prefix=prefix,
            cluster_type=NodeType.CODE_SCC_CLUSTER.value,
        )

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

    @property
    def metadata(self) -> Dict[str, Any]:
        """Get all graph metadata.

        Returns:
            Dict[str, Any]: Complete metadata dictionary.
        """
        return self._metadata

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

    def predecessors(self, node_id: str):
        """Get predecessors of a node.

        Args:
            node_id: Node identifier.

        Returns:
            Iterable[str]: Predecessor iterator.
        """
        return self._backend.predecessors(node_id)

    def successors(self, node_id: str):
        """Get successors of a node.

        Args:
            node_id: Node identifier.

        Returns:
            Iterable[str]: Successor iterator.
        """
        return self._backend.successors(node_id)

    def out_edges(self, node_id: str, data: bool = False):
        """Get outgoing edges of a node.

        Args:
            node_id: Node identifier.
            data: If True, include edge attributes.

        Returns:
            Iterable: Edge iterator.
        """
        return self._backend.native_graph.out_edges(node_id, data=data)

    def in_edges(self, node_id: str, data: bool = False):
        """Get incoming edges of a node.

        Args:
            node_id: Node identifier.
            data: If True, include edge attributes.

        Returns:
            Iterable: Edge iterator.
        """
        return self._backend.native_graph.in_edges(node_id, data=data)

    def remove_edge(self, source: str, target: str, key: int) -> None:
        """Remove an edge from the graph.

        Args:
            source: Source node ID.
            target: Target node ID.
            key: Edge key.
        """
        self._backend.native_graph.remove_edge(source, target, key)
        logger.debug("Removed edge: %s -> %s (key=%d)", source, target, key)

    def derive_asset_artifact_projection(
        self,
        config: Optional[ProjectionConfig] = None,
        *,
        enable_fallback: Optional[bool] = None,
        enable_link_closure: Optional[bool] = None,
        enable_header_closure: Optional[bool] = None,
        max_header_hops: Optional[int] = None,
        ignore_external_placeholders_in_fallback: Optional[bool] = None,
        nearest_dir_min_evidence: Optional[int] = None,
        link_closure_max_hops: Optional[int] = None,
        resolve_include_placeholders: Optional[bool] = None,
        fuse_evidence: Optional[bool] = None,
        fallback_max_targets_per_node: Optional[int] = None,
        fallback_disable_import_inherited: Optional[bool] = None,
        fallback_disable_nearest_dir: Optional[bool] = None,
    ) -> None:
        """Derive asset→artifact projection edges.

        This builds structural edges for analysis from existing relationships.
        It preserves previous behavior by default and adds optional closures.

        Args:
            config: Optional ProjectionConfig instance supplying default values.
            enable_fallback: Enable conservative fallbacks for unmatched assets.
            enable_link_closure: Propagate influence across link_libraries edges.
            enable_header_closure: Propagate include effects using multi-hop closure.
            max_header_hops: Max include hops for header closure.
            ignore_external_placeholders_in_fallback: Skip external placeholders
                in fallback.
            nearest_dir_min_evidence: Minimal source evidence for nearest-dir fallback.
            link_closure_max_hops: Max hops across artifact links during link closure.
            resolve_include_placeholders: Resolve //include placeholders via include_dirs.
            fuse_evidence: Fuse multi-evidence projection edges for compactness.
            fallback_max_targets_per_node: Cap fallback targets attached per asset.
            fallback_disable_import_inherited: Skip import/include inheritance fallback.
            fallback_disable_nearest_dir: Skip nearest-directory fallback.

        Returns:
            None
        """
        _derive_projection(
            self,
            config=config,
            enable_fallback=enable_fallback,
            enable_link_closure=enable_link_closure,
            enable_header_closure=enable_header_closure,
            max_header_hops=max_header_hops,
            ignore_external_placeholders_in_fallback=ignore_external_placeholders_in_fallback,
            nearest_dir_min_evidence=nearest_dir_min_evidence,
            link_closure_max_hops=link_closure_max_hops,
            resolve_include_placeholders=resolve_include_placeholders,
            fuse_evidence=fuse_evidence,
            fallback_max_targets_per_node=fallback_max_targets_per_node,
            fallback_disable_import_inherited=fallback_disable_import_inherited,
            fallback_disable_nearest_dir=fallback_disable_nearest_dir,
        )

    def fuse_projection_evidence(self, kinds: Optional[Set[str]] = None) -> None:
        """Fuse multi-evidence projection edges per (u, v, kind).

        Args:
            kinds: Which kinds to fuse; defaults to {'part_of', 'affects'}.

        Returns:
            None
        """
        _fuse_projection(self, kinds=kinds)

    def save(self, file_path: Path, file_format: str = "json") -> None:
        """Save graph to file.

        Args:
            file_path: Output file path.
            file_format: Output format ('json' or 'gml').
        """
        file_path = Path(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        if file_format == "json":
            data = nx.readwrite.json_graph.node_link_data(
                self._backend.native_graph, edges="edges"
            )
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info("Graph saved to: %s", file_path)
        elif file_format == "gml":
            nx.write_gml(self._backend.native_graph, str(file_path))
            logger.info("Graph saved to: %s", file_path)
        else:
            raise ValueError(f"Unsupported format: {file_format}")

    @classmethod
    def load(cls, file_path: Path, file_format: str = "json") -> "GraphManager":
        """Load graph from file.

        Args:
            file_path: Input file path.
            file_format: Input format ('json' or 'gml').

        Returns:
            GraphManager: Loaded graph manager.
        """
        manager = cls()

        if file_format == "json":
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            manager._backend.set_native_graph(
                nx.readwrite.json_graph.node_link_graph(data, edges="edges")
            )
            logger.info("Graph loaded from: %s", file_path)
        elif file_format == "gml":
            manager._backend.set_native_graph(nx.read_gml(str(file_path)))
            logger.info("Graph loaded from: %s", file_path)
        else:
            raise ValueError(f"Unsupported format: {file_format}")

        return manager
