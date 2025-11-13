"""Graph manager for single transaction.

GraphManager is the unique graph instance within a transaction,
managing all nodes, edges, and metadata for that analysis session.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from depanalyzer.graph.backend import GraphBackend

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
        *,
        enable_fallback: bool = True,
        enable_link_closure: bool = False,
        enable_header_closure: bool = False,
        max_header_hops: int = 2,
        ignore_external_placeholders_in_fallback: bool = False,
        nearest_dir_min_evidence: int = 0,
        link_closure_max_hops: int = 3,
        resolve_include_placeholders: bool = False,
        fuse_evidence: bool = False,
        fallback_max_targets_per_node: Optional[int] = None,
        fallback_disable_import_inherited: bool = False,
        fallback_disable_nearest_dir: bool = False,
    ) -> None:
        """Derive asset→artifact projection edges.

        This builds structural edges for analysis from existing relationships.
        It preserves previous behavior by default and adds optional closures.

        Args:
            enable_fallback: Enable conservative fallbacks for unmatched assets.
            enable_link_closure: Propagate influence across link_libraries edges.
            enable_header_closure: Propagate include effects using multi-hop closure.
            max_header_hops: Max include hops for header closure.
            ignore_external_placeholders_in_fallback: Skip external placeholders in fallback.
            nearest_dir_min_evidence: Minimal source evidence to accept nearest-dir fallback.
            link_closure_max_hops: Max hops across artifact links during link closure.
            resolve_include_placeholders: Resolve //include:*.h to real headers using include_dirs.
            fuse_evidence: Fuse multi-evidence projection edges for compactness.
            fallback_max_targets_per_node: If set (>0), cap number of fallback targets
                attached per asset in the import/include inheritance step (stable order).
            fallback_disable_import_inherited: Skip import/include inheritance fallback when True.
            fallback_disable_nearest_dir: Skip nearest-by-directory fallback when True.

        Returns:
            None
        """
        from collections import defaultdict

        graph = self._backend.native_graph

        def edge_kind(data: Dict) -> Optional[str]:
            """Resolve semantic kind of an edge.

            Args:
                data: Edge attribute mapping.

            Returns:
                Optional[str]: Semantic kind from 'kind'/'label'/'type'.
            """
            if not isinstance(data, Dict):
                return None
            return data.get("kind") or data.get("label") or data.get("type")

        # Preprocess: establish subdirectory → target contains edges
        subdirs = {}  # dir_id -> src_path
        artifact_types = {
            "shared_library",
            "static_library",
            "executable",
            "module",
            "module_library",
            "interface_library",
            "object",
        }

        for node, nd in graph.nodes(data=True):
            if nd.get("type") == "subdirectory":
                src_path = nd.get("src_path")
                if src_path:
                    subdirs[node] = src_path.replace("\\", "/")

        # Find targets that belong to each subdirectory
        for node, nd in graph.nodes(data=True):
            if nd.get("type") in artifact_types:
                target_src_path = nd.get("src_path")
                if not target_src_path:
                    continue
                target_src_path = target_src_path.replace("\\", "/")

                best_subdir = None
                best_subdir_len = -1
                for subdir_id, subdir_path in subdirs.items():
                    if target_src_path.startswith(subdir_path + "/"):
                        if len(subdir_path) > best_subdir_len:
                            best_subdir = subdir_id
                            best_subdir_len = len(subdir_path)

                if best_subdir:
                    self.add_edge(
                        best_subdir,
                        node,
                        edge_kind="contains",
                        parser_name="projection",
                        derived_from="subdirectory_inference",
                        confidence=1.0,
                    )

        # 1) Build part_of from explicit sources and contains
        part_of_added = set()
        for u, v, data in graph.edges(data=True):
            k = edge_kind(data)
            if k == "sources":
                self.add_edge(
                    v,
                    u,
                    edge_kind="part_of",
                    parser_name="projection",
                    derived_from="sources",
                    confidence=1.0,
                )
                part_of_added.add((v, u))
            elif k == "contains":
                self.add_edge(
                    v,
                    u,
                    edge_kind="part_of",
                    parser_name="projection",
                    derived_from="contains",
                    confidence=1.0,
                )
                part_of_added.add((v, u))

        # Build lookup: code -> {artifacts}
        code_to_artifacts: Dict[str, Set[str]] = {}
        for x, y, data in graph.edges(data=True):
            if edge_kind(data) == "part_of":
                code_to_artifacts.setdefault(x, set()).add(y)

        # 2) include propagation: header -> target via code (skip system headers)
        nodes_data_all = dict(graph.nodes(data=True))
        for u, v, data in graph.edges(data=True):
            if edge_kind(data) == "include":
                code_node = u
                header_node = v
                header_type = (nodes_data_all.get(header_node, {}) or {}).get("type")
                if header_type == "system_header" or str(header_node).startswith("//system:"):
                    continue
                for tgt in code_to_artifacts.get(code_node, set()):
                    self.add_edge(
                        header_node,
                        tgt,
                        edge_kind="affects",
                        parser_name="projection",
                        derived_from="include+part_of",
                        confidence=0.8,
                    )

        # Optional: link-closure propagation (code affects upstream consumers)
        if enable_link_closure:
            rev_adj: Dict[str, Set[str]] = defaultdict(set)
            nodes_data = dict(graph.nodes(data=True))
            for u, v, data in graph.edges(data=True):
                if (
                    edge_kind(data) == "link_libraries"
                    and nodes_data.get(u, {}).get("type") in artifact_types
                ):
                    if nodes_data.get(v, {}).get("type") in artifact_types:
                        rev_adj[v].add(u)

            for code, bases in list(code_to_artifacts.items()):
                for base in list(bases):
                    frontier = [(base, 0)]
                    seen = {base}
                    while frontier:
                        cur, d = frontier.pop(0)
                        if d >= link_closure_max_hops:
                            continue
                        for nxt in rev_adj.get(cur, set()):
                            if nxt in seen:
                                continue
                            seen.add(nxt)
                            conf = max(0.5, 0.7 - 0.1 * d)
                            self.add_edge(
                                code,
                                nxt,
                                edge_kind="affects",
                                parser_name="projection",
                                derived_from="link_closure",
                                confidence=conf,
                                hops=d + 1,
                            )
                            frontier.append((nxt, d + 1))

        # Optional: header/include multi-hop closure
        if enable_header_closure and max_header_hops > 0:
            inc_adj: Dict[str, Set[str]] = defaultdict(set)
            for u, v, data in graph.edges(data=True):
                if edge_kind(data) == "include":
                    inc_adj[u].add(v)

            node_to_artifacts: Dict[str, Set[str]] = defaultdict(set)
            for u, v, data in graph.edges(data=True):
                k = edge_kind(data)
                if k in {"part_of", "affects"}:
                    node_to_artifacts[u].add(v)

            for start, arts in list(node_to_artifacts.items()):
                if not arts:
                    continue
                st_type = (nodes_data_all.get(start, {}) or {}).get("type")
                if st_type == "system_header" or str(start).startswith("//system:"):
                    continue
                frontier = [(start, 0)]
                visited = {start}
                while frontier:
                    cur, d = frontier.pop(0)
                    if d >= max_header_hops:
                        continue
                    for nxt in inc_adj.get(cur, set()):
                        if nxt in visited:
                            continue
                        visited.add(nxt)
                        ntype = (nodes_data_all.get(nxt, {}) or {}).get("type")
                        if not (ntype == "system_header" or str(nxt).startswith("//system:")):
                            conf = max(0.5, 0.6 - 0.1 * d)
                            for tgt in arts:
                                self.add_edge(
                                    nxt,
                                    tgt,
                                    edge_kind="affects",
                                    parser_name="projection",
                                    derived_from="include_closure",
                                    confidence=conf,
                                    hops=d + 1,
                                )
                        frontier.append((nxt, d + 1))

        # Optional: resolve //include:*.h placeholders
        if resolve_include_placeholders:
            name_index: Dict[str, Set[str]] = defaultdict(set)
            for n, nd in graph.nodes(data=True):
                if not isinstance(n, str) or not n.startswith("//"):
                    continue
                if (nd.get("type") == "code") and (
                    n.split(":")[0].lower().endswith((".h", ".hpp", ".hh", ".hxx"))
                ):
                    base = n.split("/")[-1].split(":")[0]
                    name_index[base].add(n)

            artifact_include_dirs: Dict[str, List[str]] = {}
            for n, nd in graph.nodes(data=True):
                incs = nd.get("include_dirs") or []
                if incs and isinstance(incs, list):
                    artifact_include_dirs[n] = [
                        i.replace("\\", "/").lstrip("/")
                        for i in incs
                        if isinstance(i, str)
                    ]

            for u, v, data in list(graph.edges(data=True)):
                if edge_kind(data) != "include":
                    continue
                if not (isinstance(v, str) and v.startswith("//include:")):
                    continue
                base = v.split(":", 1)[-1]
                candidates = name_index.get(base)
                if not candidates:
                    continue
                targets = code_to_artifacts.get(u, set())
                for tgt in targets:
                    inc_dirs = artifact_include_dirs.get(tgt) or []
                    for cand in candidates:
                        cand_dir = cand[2:].split(":")[0]
                        if "/" in cand_dir:
                            cand_dir = cand_dir.rsplit("/", 1)[0]
                        for inc in inc_dirs:
                            if cand_dir.startswith(inc.lstrip("/")):
                                self.add_edge(
                                    cand,
                                    tgt,
                                    edge_kind="affects",
                                    parser_name="projection",
                                    derived_from="include_dir_resolve",
                                    confidence=0.75,
                                )
                                break

        if not enable_fallback:
            if fuse_evidence:
                self.fuse_projection_evidence()
            return

        # Identify artifact nodes and their directories
        artifact_dirs: Dict[str, str] = {}
        for node, nd in graph.nodes(data=True):
            if nd.get("type") in artifact_types:
                src_path = nd.get("src_path")
                if isinstance(src_path, str) and src_path:
                    try:
                        import os
                        artifact_dirs[node] = os.path.dirname(src_path.replace("\\", "/"))
                    except Exception:
                        pass
                else:
                    if isinstance(node, str) and node.startswith("//"):
                        path_part = node[2:].split(":")[0]
                        artifact_dirs[node] = (
                            path_part.rsplit("/", 1)[0] if "/" in path_part else path_part
                        )

        def has_part_of(n: str) -> bool:
            for a, b, ed in graph.out_edges(n, data=True):
                if edge_kind(ed) == "part_of":
                    return True
            return False

        # 3) Fallback: inherit from importers' artifacts
        if not fallback_disable_import_inherited:
            for node, nd in list(graph.nodes(data=True)):
                ntype = nd.get("type")
                if ntype not in {"code", "project_header", "header"}:
                    continue
                if ntype == "system_header" or str(node).startswith("//system:"):
                    continue
                if ignore_external_placeholders_in_fallback:
                    origin = nd.get("origin")
                    prov = nd.get("provenance")
                    if (
                        str(node).startswith("//external:")
                        or origin == "external"
                        or prov == "import_unresolved"
                    ):
                        continue
                if has_part_of(node):
                    continue
                candidate_targets_list: List[str] = []
                candidate_targets_seen: Set[str] = set()
                for pred, _, ed in graph.in_edges(node, data=True):
                    k = edge_kind(ed)
                    if k in {"import", "include"}:
                        for tgt in code_to_artifacts.get(pred, set()):
                            if tgt not in candidate_targets_seen:
                                candidate_targets_seen.add(tgt)
                                candidate_targets_list.append(tgt)
                if (
                    isinstance(fallback_max_targets_per_node, int)
                    and fallback_max_targets_per_node > 0
                ):
                    candidate_targets_list = candidate_targets_list[:fallback_max_targets_per_node]
                for tgt in candidate_targets_list:
                    self.add_edge(
                        node,
                        tgt,
                        edge_kind="affects",
                        parser_name="projection",
                        derived_from="fallback:import_inherited",
                        confidence=0.6,
                        fallback_attached=True,
                        fallback_rule="import_inherited",
                    )

        # 4) Fallback: nearest-by-directory artifact
        def best_dir_match(node_label: str) -> Optional[str]:
            if not (isinstance(node_label, str) and node_label.startswith("//")):
                return None
            node_dir = node_label[2:]
            if ":" in node_dir:
                node_dir = node_dir.split(":")[0]
            if "/" in node_dir:
                node_dir = node_dir.rsplit("/", 1)[0]
            best = None
            best_len = -1
            for tgt, tdir in artifact_dirs.items():
                if not tdir:
                    continue
                nd = node_dir.replace("\\", "/")
                td = tdir.replace("\\", "/").lstrip("/")
                if nd.startswith(td) and len(td) > best_len:
                    if nearest_dir_min_evidence > 0:
                        has_evidence = False
                        for code_node, arts in code_to_artifacts.items():
                            if tgt in arts and code_node.startswith("//"):
                                cdir = code_node[2:].split(":")[0]
                                if "/" in cdir:
                                    cdir = cdir.rsplit("/", 1)[0]
                                if cdir.replace("\\", "/").startswith(nd):
                                    has_evidence = True
                                    break
                        if not has_evidence:
                            continue
                    best = tgt
                    best_len = len(td)
            return best

        if not fallback_disable_nearest_dir:
            for node, nd in list(graph.nodes(data=True)):
                ntype = nd.get("type")
                if ntype not in {"code", "project_header", "header"}:
                    continue
                if ntype == "system_header" or str(node).startswith("//system:"):
                    continue
                if has_part_of(node):
                    continue
                tgt = best_dir_match(node)
                if tgt:
                    self.add_edge(
                        node,
                        tgt,
                        edge_kind="affects",
                        parser_name="projection",
                        derived_from="fallback:nearest_dir",
                        confidence=0.5,
                        fallback_attached=True,
                        fallback_rule="nearest_dir",
                    )
        if fuse_evidence:
            self.fuse_projection_evidence()

    def fuse_projection_evidence(self, kinds: Optional[Set[str]] = None) -> None:
        """Fuse multi-evidence projection edges per (u,v,kind).

        This collapses multiple 'projection' edges between the same endpoints and
        of the same semantic kind into a single edge that merges confidences and
        provenance. It preserves non-projection edges untouched.

        Args:
            kinds: Which kinds to fuse; defaults to {'part_of','affects'}.

        Returns:
            None
        """
        from collections import defaultdict

        if kinds is None:
            kinds = {"part_of", "affects"}

        graph = self._backend.native_graph
        groups: Dict[tuple[str, str, str], List[tuple[str, str, int, Dict]]] = defaultdict(list)

        for u, v, key, data in graph.edges(data=True, keys=True):
            if not isinstance(data, Dict):
                continue
            if data.get("parser_name") != "projection":
                continue
            k = data.get("kind") or data.get("label") or data.get("type")
            if k not in kinds:
                continue
            groups[(u, v, k)].append((u, v, key, data))

        for (u, v, k), items in groups.items():
            if len(items) <= 1:
                continue
            max_conf = 0.0
            derived_from: Set[str] = set()
            fallback_any = False
            rules: Set[str] = set()
            for _, _, _, d in items:
                try:
                    c = float(d.get("confidence", 0.0))
                    if c > max_conf:
                        max_conf = c
                except Exception:
                    pass
                df = d.get("derived_from")
                if isinstance(df, str):
                    derived_from.add(df)
                elif isinstance(df, (list, tuple)):
                    for x in df:
                        if isinstance(x, str):
                            derived_from.add(x)
                if d.get("fallback_attached"):
                    fallback_any = True
                fr = d.get("fallback_rule")
                if isinstance(fr, str):
                    rules.add(fr)

            # remove all old edges
            for _, _, key, _ in items:
                try:
                    self.remove_edge(u, v, key)
                except Exception:
                    continue

            # add a fused edge
            attrs = {"kind": k, "parser_name": "projection"}
            if max_conf:
                attrs["confidence"] = max_conf
            if derived_from:
                attrs["derived_from"] = sorted(derived_from)
            if fallback_any:
                attrs["fallback_attached"] = True
            if rules:
                attrs["fallback_rule"] = sorted(rules)
            self.add_edge(u, v, edge_kind=k, **attrs)

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
