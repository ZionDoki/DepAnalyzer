"""Graph manager for single transaction.

GraphManager is the unique graph instance within a transaction,
managing all nodes, edges, and metadata for that analysis session.
"""

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

import networkx as nx

from depanalyzer.utils.path_utils import normalize_node_id

from .backend import GraphBackend, NetworkXBackend
from ..models.schema import (
    EdgeKind,
    EdgeSpec,
    NodeSpec,
    NodeType,
    validate_edge,
    validate_node,
)
from ..ops.condensation import CondensationResult, build_condensation_dag
from ..ops.projection import (
    derive_asset_artifact_projection as _derive_projection,
    fuse_projection_evidence as _fuse_projection,
)
from ..projection_config import ProjectionConfig

logger = logging.getLogger("depanalyzer.graph.core.manager")


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
        backend: Optional[GraphBackend] = None,
    ) -> None:
        """Initialize graph manager.

        Args:
            graph_id: Optional graph identifier.
            root_path: Optional root path for node ID normalization.
            backend: Optional graph backend. Defaults to NetworkXBackend.
        """
        self.graph_id = graph_id or "default"
        self.root_path = Path(root_path).resolve() if root_path else None

        self.path_namespace = path_namespace
        self._backend: GraphBackend = backend or NetworkXBackend()
        self._metadata: Dict[str, Any] = {}

        # Lazy loading registry for external graphs (graph_id -> Path)
        self._external_graph_registry: Dict[str, Path] = {}

        # Node existence cache for performance optimization
        # This avoids repeated has_node() calls to the backend
        self._node_cache: Set[str] = set()

        # Thread safety: RLock for concurrent access from multiple threads
        # (e.g., Worker ThreadPoolExecutor + ParsePhase consumer thread)
        self._lock = threading.RLock()

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

    def register_external_graph(self, graph_id: str, path: Path) -> None:
        """Register an external graph for potential lazy loading.

        Args:
            graph_id: Unique identifier of the external graph.
            path: File path to the graph file.
        """
        with self._lock:
            self._external_graph_registry[graph_id] = path
        logger.debug("Registered external graph %s at %s", graph_id, path)

    def mount_external_node(self, node_id: str, external_graph_id: str, **attributes) -> None:
        """Create a proxy node that points to a node in an external graph.
        
        Args:
            node_id: The node ID in the current graph.
            external_graph_id: The ID of the graph where the real definition lives.
            **attributes: Additional attributes.
        """
        self.add_node(
            node_id, 
            NodeType.PROXY.value, 
            external_graph_id=external_graph_id,
            **attributes
        )
        
    def lazy_load_subgraph(
        self, graph_id: str, _depth: int = 0, max_depth: int = 10
    ) -> Optional["GraphManager"]:
        """Load a referenced external graph.

        This is a placeholder for the "Graph of Graphs" logic.
        In a full implementation, this would load the graph from disk (or cache)
        and potentially merge relevant parts or return it for query.

        Args:
            graph_id: ID of the graph to load.
            _depth: Current recursion depth (internal use).
            max_depth: Maximum recursion depth to prevent stack overflow.

        Returns:
            GraphManager containing the loaded graph, or None if not found.
        """
        if _depth >= max_depth:
            logger.warning(
                "Max recursion depth (%d) reached loading graph: %s",
                max_depth,
                graph_id,
            )
            return None

        if graph_id not in self._external_graph_registry:
            logger.warning("Attempted to load unknown external graph: %s", graph_id)
            return None

        path = self._external_graph_registry[graph_id]
        try:
            # Recursively load the external graph
            # Note: deeply nested loading policies should be handled by the caller
            return GraphManager.load(path)
        except Exception as e:
            logger.error("Failed to lazy load graph %s from %s: %s", graph_id, path, e)
            return None

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

        This method is thread-safe. The existence check and node addition
        are performed atomically under a lock to prevent TOCTOU race conditions.

        Args:
            node_id: Unique node identifier.
            node_type: Node type (see NodeType constants).
            confidence: Confidence score (0.0-1.0).
            over_approx: Whether this is an over-approximation.
            evidence: List of evidence sources.
            **attributes: Additional node attributes.
        """
        # Note: We skip the lock-free cache check here to avoid TOCTOU race conditions.
        # The add_node_spec method performs the authoritative check under lock.

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

        # Extract explicit schema fields that might be in attributes
        external_graph_id = attrs.pop("external_graph_id", None)

        spec = NodeSpec(
            id=node_id,
            type=ntype,
            confidence=confidence,
            over_approx=over_approx,
            label=attrs.pop("label", None),
            src_path=attrs.get("src_path"),
            name=attrs.get("name"),
            parser_name=attrs.get("parser_name"),
            external_graph_id=external_graph_id,
            attrs=attrs,
        )
        self.add_node_spec(spec)

    def _has_node_cached(self, node_id: str) -> bool:
        """Check if node exists using cache for performance.

        This method uses an in-memory cache to avoid repeated backend lookups.
        The cache is populated on first access and updated on node additions.

        Args:
            node_id: Node identifier to check.

        Returns:
            bool: True if node exists.
        """
        if node_id in self._node_cache:
            return True
        if self._backend.has_node(node_id):
            self._node_cache.add(node_id)
            return True
        return False

    def add_node_spec(self, spec: NodeSpec) -> None:
        """Add a node described by a NodeSpec.

        This is the preferred entry point for new code constructing nodes.
        """
        with self._lock:
            if self._has_node_cached(spec.id):
                logger.debug("Node %s already exists, skipping", spec.id)
                return

            validate_node(spec, self.root_path)
            self._backend.add_node(spec.id, **spec.to_backend_attrs())
            self._node_cache.add(spec.id)
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

    def _ensure_node_exists(self, node_id: str) -> None:
        """Ensure a node exists, creating a provisional one if needed.

        Args:
            node_id: Node identifier to ensure exists.
        """
        if not self._has_node_cached(node_id):
            provisional_node = NodeSpec(
                id=node_id,
                type=NodeType.UNKNOWN,
                provisional=True,
                attrs={},
            )
            validate_node(provisional_node, self.root_path)
            self._backend.add_node(node_id, **provisional_node.to_backend_attrs())
            self._node_cache.add(node_id)

    def add_edge_spec(self, spec: EdgeSpec) -> int:
        """Add an edge described by an EdgeSpec.

        This is the preferred entry point for new code constructing edges.
        """
        with self._lock:
            self._ensure_node_exists(spec.source)
            self._ensure_node_exists(spec.target)

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

    def add_edges_batch(self, specs: List[EdgeSpec]) -> List[int]:
        """Add multiple edges in batch for improved performance.

        This method optimizes bulk edge additions by:
        1. Pre-collecting all required nodes
        2. Creating missing nodes in one pass
        3. Adding all edges

        Args:
            specs: List of EdgeSpec objects to add.

        Returns:
            List[int]: List of edge keys for the added edges.
        """
        if not specs:
            return []

        with self._lock:
            # Collect all unique node IDs needed
            needed_nodes: Set[str] = set()
            for spec in specs:
                needed_nodes.add(spec.source)
                needed_nodes.add(spec.target)

            # Find nodes that need to be created
            missing_nodes = needed_nodes - self._node_cache
            for node_id in missing_nodes:
                if not self._backend.has_node(node_id):
                    provisional_node = NodeSpec(
                        id=node_id,
                        type=NodeType.UNKNOWN,
                        provisional=True,
                        attrs={},
                    )
                    validate_node(provisional_node, self.root_path)
                    self._backend.add_node(node_id, **provisional_node.to_backend_attrs())
                self._node_cache.add(node_id)

            # Add all edges
            keys: List[int] = []
            for spec in specs:
                validate_edge(spec)
                key = self._backend.add_edge(
                    spec.source,
                    spec.target,
                    **spec.to_backend_attrs(),
                )
                keys.append(key)

        logger.debug("Added %d edges in batch", len(specs))
        return keys

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
        with self._lock:
            if not self.has_node(node_id):
                raise ValueError(f"Node {node_id} does not exist")
            self._backend.update_node(node_id, **{key: value})
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
            self._as_networkx_graph(),
            node_prefix=prefix,
            cluster_type=NodeType.CODE_SCC_CLUSTER.value,
        )

    def set_metadata(self, key: str, value: Any) -> None:
        """Set graph metadata.

        Args:
            key: Metadata key.
            value: Metadata value.
        """
        with self._lock:
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

    def in_degree(self, node_id: str) -> int:
        """Get in-degree of a node.

        Args:
            node_id: Node identifier.

        Returns:
            int: Number of incoming edges.
        """
        return self._backend.in_degree(node_id)

    def out_degree(self, node_id: str) -> int:
        """Get out-degree of a node.

        Args:
            node_id: Node identifier.

        Returns:
            int: Number of outgoing edges.
        """
        return self._backend.out_degree(node_id)

    def remove_node(self, node_id: str) -> None:
        """Remove a node from the graph.

        Args:
            node_id: Node identifier.
        """
        with self._lock:
            self._backend.remove_node(node_id)
            self._node_cache.discard(node_id)
        logger.debug("Removed node: %s", node_id)

    def out_edges(self, node_id: str, data: bool = False, keys: bool = False):
        """Get outgoing edges of a node.

        Args:
            node_id: Node identifier.
            data: If True, include edge attributes.
            keys: If True, include edge keys.

        Returns:
            Iterable: Edge iterator.
        """
        return self._backend.out_edges(node_id, data=data, keys=keys)

    def in_edges(self, node_id: str, data: bool = False, keys: bool = False):
        """Get incoming edges of a node.

        Args:
            node_id: Node identifier.
            data: If True, include edge attributes.
            keys: If True, include edge keys.

        Returns:
            Iterable: Edge iterator.
        """
        return self._backend.in_edges(node_id, data=data, keys=keys)

    def remove_edge(self, source: str, target: str, key: Optional[int] = None) -> None:
        """Remove an edge from the graph.

        Args:
            source: Source node ID.
            target: Target node ID.
            key: Edge key (optional for MultiDiGraph).
        """
        with self._lock:
            self._backend.remove_edge(source, target, key)
        logger.debug("Removed edge: %s -> %s (key=%s)", source, target, key)

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

        graph = self._as_networkx_graph()
        if file_format == "json":
            data = nx.readwrite.json_graph.node_link_data(
                graph, edges="edges"
            )
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info("Graph saved to: %s", file_path)
        elif file_format == "gml":
            nx.write_gml(graph, str(file_path))
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
        manager = cls(backend=NetworkXBackend())

        if file_format == "json":
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            edge_key = "edges" if isinstance(data, dict) and "edges" in data else "links"
            manager._backend.set_native_graph(
                nx.readwrite.json_graph.node_link_graph(data, edges=edge_key)
            )
            logger.info("Graph loaded from: %s", file_path)
        elif file_format == "gml":
            manager._backend.set_native_graph(nx.read_gml(str(file_path)))
            logger.info("Graph loaded from: %s", file_path)
        else:
            raise ValueError(f"Unsupported format: {file_format}")

        return manager

    def _as_networkx_graph(self) -> nx.MultiDiGraph:
        """Return a NetworkX MultiDiGraph view of the backend."""
        if isinstance(self._backend, NetworkXBackend):
            return self._backend.native_graph

        nx_graph = nx.MultiDiGraph()
        try:
            for node_item in self._backend.nodes(data=True):
                if isinstance(node_item, tuple) and len(node_item) == 2:
                    nid, attrs = node_item
                    nx_graph.add_node(nid, **(attrs or {}))
                else:
                    nx_graph.add_node(node_item)

            for edge_item in self._backend.edges(data=True, keys=True):
                if len(edge_item) == 4:
                    u, v, key, attrs = edge_item
                    nx_graph.add_edge(u, v, key=key, **(attrs or {}))
                elif len(edge_item) == 3:
                    u, v, attrs = edge_item
                    nx_graph.add_edge(u, v, **(attrs or {}))
                elif len(edge_item) == 2:
                    u, v = edge_item
                    nx_graph.add_edge(u, v)
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.error("Failed to materialize backend as NetworkX graph: %s", exc)
            raise

        return nx_graph
