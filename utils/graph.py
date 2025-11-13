#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# Copyright (c) 2024 Lanzhou University
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""
TODO: need write ut for this module @Zihao
"""

import os
import json
import gzip
import base64
import warnings
from collections import defaultdict
from typing import Iterator, Optional, MutableMapping, Mapping, Any

import networkx as nx

EdgeIndex = tuple[str, str, Optional[int]]


class Vertex(dict):
    """
    A wrapper for node in networkx graph, the label is the only required parameter,
    other parameters are optional.
    """

    def __init__(self, label: str, **kwargs) -> None:
        """
        init a node object that can be added to the networkx graph.

        Args:
            label (str): the label of the node.
            **kwargs: the other parameters of the node.
        """
        super().__init__({"node_for_adding": label, **self._filter(kwargs)})

    @property
    def label(self) -> str:
        """get the label of the node."""
        return self["node_for_adding"]

    def _filter(self, kwargs):
        """filter the None value in the kwargs."""
        return {k: v for k, v in kwargs.items() if v is not None}


class Edge(dict):
    """
    A wrapper for edge in networkx graph, the u and v are the only required parameter,
    other parameters are optional.
    """

    def __init__(self, u: str, v: str, **kwargs) -> None:
        """
        init a edge object that can be added to the networkx graph.

        Args:
            u (str): the source node of the edge.
            v (str): the target node of the edge.
            **kwargs: the other parameters of the edge.
        """

        super().__init__(
            {"u_for_edge": str(u), "v_for_edge": str(v), **self._filter(kwargs)}
        )

    @property
    def index(self) -> EdgeIndex:
        """get the index (EdgeIndex) of the edge in the graph."""
        return (self["u_for_edge"], self["v_for_edge"], self.get("key", -1))

    def _filter(self, kwargs):
        """filter the None value in the kwargs."""
        return {k: v for k, v in kwargs.items() if v is not None}


class Triple:
    """
    A wrapper for the triple of (Vertex, Edge, Vertex) in networkx graph.
    """

    def __init__(
        self, source: Vertex, target: Vertex, edge: Edge | None = None, **kwargs
    ) -> None:
        """
        init a triple object that can be added to the networkx graph.

        Args:
            source (Vertex): the source node of the edge.
            target (Vertex): the target node of the edge.
            edge (Edge): the edge object of the edge.
            **kwargs: the other parameters of the edge.
        """
        self.source = source
        self.target = target
        if edge:
            self.edge = edge
        else:
            self.edge = Edge(
                source["node_for_adding"], target["node_for_adding"], **kwargs
            )


class GraphManager:
    """
    A wrapper for networkx graph, the graph is a MultiDiGraph object.
    """

    _edge_keys_to_exclude: set = {"u_for_edge", "v_for_edge", "key"}

    def __init__(self, file_path: str | None = None) -> None:
        """
        Create Graph structure that can be used to store the graph data.
        It can be initialized from a file or a new graph, and save it to a file.

        Args:
            file_path (str, optional): the file path of the graph. Defaults to None.
        """
        self.graph = nx.MultiDiGraph()

        if not file_path:
            return

        if not os.path.exists(file_path):
            warnings.warn(f"{file_path} not exists, create a new graph")
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.graph = nx.readwrite.json_graph.node_link_graph(data, edges="edges")
        except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
            self.graph = nx.read_gml(file_path)

    def nodes(self, **kwargs):
        """wrapper for the nodes of the networkx."""
        return self.graph.nodes(**kwargs)

    def edges(self, **kwargs):
        """wrapper for the edges of the networkx."""
        return self.graph.edges(**kwargs)

    @property
    def root_nodes(self) -> list:
        """
        Get all root nodes (nodes with in-degree = 0).

        Returns:
            list: List of root node labels

        Note: Computed on every access to ensure correctness when graph is modified.
        """
        return [node for node in self.graph.nodes if self.graph.in_degree(node) == 0]

    @property
    def leaf_nodes(self) -> list:
        """
        Get all leaf nodes (nodes with out-degree = 0).

        Returns:
            list: List of leaf node labels

        Note: Computed on every access to ensure correctness when graph is modified.
        """
        return [node for node in self.graph.nodes if self.graph.out_degree(node) == 0]

    def deduplicate_and_reorder_edges(self) -> "GraphManager":
        """Create a new graph with deduplicated and ordered edges.

        Args:
            None

        Returns:
            GraphManager: A new manager containing a deduplicated MultiDiGraph.
        """
        seen = set()
        new_keys = defaultdict(int)
        new_graph = nx.MultiDiGraph()

        for u, v, _, data in self.graph.edges(data=True, keys=True):
            edge_tuple = (u, v, frozenset(data.items()))
            if edge_tuple not in seen:
                seen.add(edge_tuple)
                new_key = new_keys[(u, v)]
                new_keys[(u, v)] += 1
                new_graph.add_edge(u, v, key=new_key, **data)

        new_graph_manager = GraphManager()
        new_graph_manager.graph = new_graph
        return new_graph_manager

    def dfs(self):
        """Return a DFS tree view of the graph.

        Args:
            None

        Returns:
            Any: The DFS tree produced by networkx.dfs_tree.
        """
        return nx.dfs_tree(self.graph)

    def successors(self, node: str) -> str:
        """Iterate successors of a node.

        Args:
            node (str): Node label.

        Returns:
            Iterator[str]: Successor iterator from the underlying graph.
        """
        return self.graph.successors(node)

    def predecessors(self, node: str) -> str:
        """Iterate predecessors of a node.

        Args:
            node (str): Node label.

        Returns:
            Iterator[str]: Predecessor iterator from the underlying graph.
        """
        return self.graph.predecessors(node)

    def get_ancestors(self, node, depth) -> list[str]:
        """Collect ancestors up to a bounded depth.

        Args:
            node (str): Start node label.
            depth (int): Max levels to traverse upward.

        Returns:
            list[str]: Unique ancestor labels within the given depth.
        """
        current_level_nodes = [node]
        ancestors = []

        for _ in range(depth):
            next_level_nodes = []
            for n in current_level_nodes:
                preds = self.predecessors(n)
                next_level_nodes.extend(preds)
            current_level_nodes = next_level_nodes
            ancestors.extend(current_level_nodes)
        return list(set(ancestors))

    def is_leaf(self, node: str):
        """Return whether a node has no outgoing edges.

        Args:
            node (str): Node label.

        Returns:
            bool: True if out-degree is zero, otherwise False.
        """
        return self.graph.out_degree(node) == 0

    def add_triplet(self, triple: Triple):
        """Add a (source, edge, target) triple to the graph.

        Args:
            triple (Triple): Triple describing two vertices and the connecting edge.

        Returns:
            None
        """
        self.add_node(triple.source)
        self.add_node(triple.target)
        self.add_edge(triple.edge)

    def add_edge(self, edge: Edge):
        """Add an edge to the graph and backfill its key.

        Args:
            edge (Edge): Edge wrapper containing endpoints and attributes.

        Returns:
            None
        """

        # Assume self.graph.edges returns all edges in the graph

        # Auto-fill a stable semantic kind if missing, derived from label/type
        if "kind" not in edge:
            kind = edge.get("label") or edge.get("type")
            if kind is not None:
                edge.update({"kind": kind})

        if self.query_edge_by_label(**edge):
            return

        key = self.graph.add_edge(**edge)
        edge.update({"key": key})

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
        # Backward-compatible new knobs
        fallback_max_targets_per_node: Optional[int] = None,
        fallback_disable_import_inherited: bool = False,
        fallback_disable_nearest_dir: bool = False,
    ) -> None:
        """Derive asset→artifact projection edges.

        This builds structural edges for analysis from existing relationships.
        It preserves previous behavior by default and adds optional closures.

        Args:
            enable_fallback (bool): Enable conservative fallbacks for unmatched assets.
            enable_link_closure (bool): Propagate influence across link_libraries edges.
            enable_header_closure (bool): Propagate include effects using multi-hop closure.
            max_header_hops (int): Max include hops for header closure.
            ignore_external_placeholders_in_fallback (bool): Skip external placeholders in fallback.
            nearest_dir_min_evidence (int): Minimal source evidence to accept nearest-dir fallback.
            link_closure_max_hops (int): Max hops across artifact links during link closure.
            resolve_include_placeholders (bool): Resolve //include:*.h to real headers using include_dirs.
            fuse_evidence (bool): Fuse multi-evidence projection edges for compactness.
            fallback_max_targets_per_node (Optional[int]): If set (>0), cap number of fallback targets
                attached per asset in the import/include inheritance step (stable order).
            fallback_disable_import_inherited (bool): Skip import/include inheritance fallback when True.
            fallback_disable_nearest_dir (bool): Skip nearest-by-directory fallback when True.

        Returns:
            None
        """

        def edge_kind(data: Mapping) -> Optional[str]:
            """Resolve semantic kind of an edge.

            Args:
                data (Mapping): Edge attribute mapping.

            Returns:
                Optional[str]: Semantic kind from 'kind'/'label'/'type'.
            """
            if not isinstance(data, Mapping):
                return None
            return data.get("kind") or data.get("label") or data.get("type")

        # Preprocess: establish subdirectory → target contains edges
        # This allows subdirectory nodes to act as compilation artifact containers
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

        for node, nd in self.graph.nodes(data=True):
            if nd.get("type") == "subdirectory":
                src_path = nd.get("src_path")
                if src_path:
                    # Normalize to forward slashes
                    subdirs[node] = src_path.replace("\\", "/")

        # Find targets that belong to each subdirectory
        for node, nd in self.graph.nodes(data=True):
            if nd.get("type") in artifact_types:
                target_src_path = nd.get("src_path")
                if not target_src_path:
                    continue
                target_src_path = target_src_path.replace("\\", "/")

                # Find the most specific (longest path) subdirectory that contains this target
                best_subdir = None
                best_subdir_len = -1
                for subdir_id, subdir_path in subdirs.items():
                    # Check if target's CMakeLists.txt is inside this subdirectory
                    if target_src_path.startswith(subdir_path + "/"):
                        if len(subdir_path) > best_subdir_len:
                            best_subdir = subdir_id
                            best_subdir_len = len(subdir_path)

                if best_subdir:
                    # Create subdirectory → target contains edge
                    e = Edge(
                        best_subdir,
                        node,
                        parser_name="projection",
                        kind="contains",
                        derived_from="subdirectory_inference",
                        confidence=1.0,
                    )
                    self.add_edge(e)

        # 1) Build part_of from explicit sources and contains
        part_of_added = set()
        for u, v, data in self.graph.edges(data=True):
            k = edge_kind(data)
            if k == "sources":
                # target u -> code v  ==> code v -> target u
                e = Edge(
                    v,
                    u,
                    parser_name="projection",
                    kind="part_of",
                    derived_from="sources",
                    confidence=1.0,
                )
                self.add_edge(e)
                part_of_added.add((v, u))
            elif k == "contains":
                # module u -> code v ==> code v -> module u
                e = Edge(
                    v,
                    u,
                    parser_name="projection",
                    kind="part_of",
                    derived_from="contains",
                    confidence=1.0,
                )
                self.add_edge(e)
                part_of_added.add((v, u))

        # Build lookup: code -> {artifacts}
        code_to_artifacts: dict[str, set[str]] = {}
        for x, y, data in self.graph.edges(data=True):
            if edge_kind(data) == "part_of":
                code_to_artifacts.setdefault(x, set()).add(y)

        # 2) include propagation: header -> target via code (skip system headers)
        nodes_data_all = dict(self.graph.nodes(data=True))
        for u, v, data in self.graph.edges(data=True):
            if edge_kind(data) == "include":
                code_node = u
                header_node = v
                header_type = (nodes_data_all.get(header_node, {}) or {}).get("type")
                if header_type == "system_header" or str(header_node).startswith(
                    "//system:"
                ):
                    continue
                for tgt in code_to_artifacts.get(code_node, set()):
                    e = Edge(
                        header_node,
                        tgt,
                        parser_name="projection",
                        kind="affects",
                        derived_from="include+part_of",
                        confidence=0.8,
                    )
                    self.add_edge(e)

        # Optional: link-closure propagation (code affects upstream consumers)
        if enable_link_closure:
            # Build reverse adjacency of artifacts from link_libraries edges
            # link_libraries: consumer(u) -> dependency(v)
            # We need dependency -> {consumers}
            artifact_types = {
                "shared_library",
                "static_library",
                "executable",
                "module",
                "module_library",
                "interface_library",
                "object",
            }
            rev_adj: dict[str, set[str]] = defaultdict(set)
            nodes_data = dict(self.graph.nodes(data=True))
            for u, v, data in self.graph.edges(data=True):
                if (
                    edge_kind(data) == "link_libraries"
                    and nodes_data.get(u, {}).get("type") in artifact_types
                ):
                    if nodes_data.get(v, {}).get("type") in artifact_types:
                        rev_adj[v].add(u)

            # Propagate code membership from base target upward to its consumers
            for code, bases in list(code_to_artifacts.items()):
                for base in list(bases):
                    frontier = [(base, 0)]  # (dependency, distance)
                    seen = {base}
                    while frontier:
                        cur, d = frontier.pop(0)
                        if d >= link_closure_max_hops:
                            continue
                        for nxt in rev_adj.get(cur, set()):  # nxt consumes cur
                            if nxt in seen:
                                continue
                            seen.add(nxt)
                            conf = max(0.5, 0.7 - 0.1 * d)
                            self.add_edge(
                                Edge(
                                    code,
                                    nxt,
                                    parser_name="projection",
                                    kind="affects",
                                    derived_from="link_closure",
                                    confidence=conf,
                                    hops=d + 1,
                                )
                            )
                            frontier.append((nxt, d + 1))

        # Optional: header/include multi-hop closure
        if enable_header_closure and max_header_hops > 0:
            # Build include adjacency u -> {v}
            inc_adj: dict[str, set[str]] = defaultdict(set)
            for u, v, data in self.graph.edges(data=True):
                if edge_kind(data) == "include":
                    inc_adj[u].add(v)

            # Seed: nodes that already map to artifacts (part_of/affects)
            node_to_artifacts: dict[str, set[str]] = defaultdict(set)
            for u, v, data in self.graph.edges(data=True):
                k = edge_kind(data)
                if k in {"part_of", "affects"}:
                    node_to_artifacts[u].add(v)

            # BFS from each seed along include edges up to max_header_hops
            for start, arts in list(node_to_artifacts.items()):
                if not arts:
                    continue
                # Do not start from system headers
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
                        # Skip system headers as closure targets
                        ntype = (nodes_data_all.get(nxt, {}) or {}).get("type")
                        if not (
                            ntype == "system_header" or str(nxt).startswith("//system:")
                        ):
                            # Confidence decays with depth: base 0.6 for 1-hop, min 0.5
                            conf = max(0.5, 0.6 - 0.1 * d)
                            for tgt in arts:
                                self.add_edge(
                                    Edge(
                                        nxt,
                                        tgt,
                                        parser_name="projection",
                                        kind="affects",
                                        derived_from="include_closure",
                                        confidence=conf,
                                        hops=d + 1,
                                    )
                                )
                        frontier.append((nxt, d + 1))

        # Optional: resolve //include:*.h placeholders using per-target include_dirs
        if resolve_include_placeholders:
            # Build basename index of real header/code nodes
            name_index: dict[str, set[str]] = defaultdict(set)
            for n, nd in self.graph.nodes(data=True):
                if not isinstance(n, str) or not n.startswith("//"):
                    continue
                if (nd.get("type") == "code") and (
                    n.split(":")[0].lower().endswith((".h", ".hpp", ".hh", ".hxx"))
                ):
                    base = n.split("/")[-1].split(":")[0]
                    name_index[base].add(n)

            # Preload include_dirs for artifacts
            artifact_include_dirs: dict[str, list[str]] = {}
            for n, nd in self.graph.nodes(data=True):
                incs = nd.get("include_dirs") or []
                if incs and isinstance(incs, list):
                    artifact_include_dirs[n] = [
                        i.replace("\\", "/").lstrip("/")
                        for i in incs
                        if isinstance(i, str)
                    ]

            # For each placeholder include, attach resolved header to the same targets
            for u, v, data in list(self.graph.edges(data=True)):
                if edge_kind(data) != "include":
                    continue
                # placeholder header
                if not (isinstance(v, str) and v.startswith("//include:")):
                    continue
                base = v.split(":", 1)[-1]
                candidates = name_index.get(base)
                if not candidates:
                    continue
                # targets that include code 'u'
                targets = code_to_artifacts.get(u, set())
                for tgt in targets:
                    inc_dirs = artifact_include_dirs.get(tgt) or []
                    for cand in candidates:
                        cand_dir = cand[2:].split(":")[0]
                        if "/" in cand_dir:
                            cand_dir = cand_dir.rsplit("/", 1)[0]
                        # accept if cand_dir is within any include_dir
                        for inc in inc_dirs:
                            if cand_dir.startswith(inc.lstrip("/")):
                                self.add_edge(
                                    Edge(
                                        cand,
                                        tgt,
                                        parser_name="projection",
                                        kind="affects",
                                        derived_from="include_dir_resolve",
                                        confidence=0.75,
                                    )
                                )
                                break

        if not enable_fallback:
            if fuse_evidence:
                self.fuse_projection_evidence()
            return

        # Identify artifact nodes and their directories (if available)
        artifact_types = {
            "shared_library",
            "static_library",
            "executable",
            "module",
            "module_library",
            "interface_library",
            "object",
        }
        artifact_dirs: dict[str, str] = {}
        for node, nd in self.graph.nodes(data=True):
            if nd.get("type") in artifact_types:
                src_path = nd.get("src_path")
                if isinstance(src_path, str) and src_path:
                    try:
                        import os

                        artifact_dirs[node] = os.path.dirname(
                            src_path.replace("\\", "/")
                        )
                    except Exception:
                        pass
                else:
                    # Try derive from label //path[:target]
                    if isinstance(node, str) and node.startswith("//"):
                        path_part = node[2:].split(":")[0]
                        artifact_dirs[node] = (
                            path_part.rsplit("/", 1)[0]
                            if "/" in path_part
                            else path_part
                        )

        # Helper to check if node has any part_of
        def has_part_of(n: str) -> bool:
            for a, b, ed in self.graph.out_edges(n, data=True):
                if edge_kind(ed) == "part_of":
                    return True
            return False

        # 3) Fallback: inherit from importers' artifacts (import/include)
        if not fallback_disable_import_inherited:
            for node, nd in list(self.graph.nodes(data=True)):
                ntype = nd.get("type")
                if ntype not in {"code", "project_header", "header"}:
                    continue
                # Skip system headers
                if ntype == "system_header" or str(node).startswith("//system:"):
                    continue
                # Optionally skip external/unresolved placeholders
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
                # Find predecessors via import/include
                candidate_targets_list: list[str] = []
                candidate_targets_seen: set[str] = set()
                for pred, _, ed in self.graph.in_edges(node, data=True):
                    k = edge_kind(ed)
                    if k in {"import", "include"}:
                        for tgt in code_to_artifacts.get(pred, set()):
                            if tgt not in candidate_targets_seen:
                                candidate_targets_seen.add(tgt)
                                candidate_targets_list.append(tgt)
                # Optionally cap number of fallback targets (stable order)
                if (
                    isinstance(fallback_max_targets_per_node, int)
                    and fallback_max_targets_per_node > 0
                ):
                    candidate_targets_list = candidate_targets_list[
                        :fallback_max_targets_per_node
                    ]
                for tgt in candidate_targets_list:
                    e = Edge(
                        node,
                        tgt,
                        parser_name="projection",
                        kind="affects",
                        derived_from="fallback:import_inherited",
                        confidence=0.6,
                        fallback_attached=True,
                        fallback_rule="import_inherited",
                    )
                    self.add_edge(e)

        # 4) Fallback: nearest-by-directory artifact
        def best_dir_match(node_label: str) -> str | None:
            # node label like //path/to/file
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
                # normalize to forward slashes
                nd = node_dir.replace("\\", "/")
                td = tdir.replace("\\", "/").lstrip("/")
                if nd.startswith(td) and len(td) > best_len:
                    # Optional evidence gating using existing sources
                    if nearest_dir_min_evidence > 0:
                        has_evidence = False
                        # Evidence: any code already part_of this target within the same directory prefix
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
            for node, nd in list(self.graph.nodes(data=True)):
                ntype = nd.get("type")
                if ntype not in {"code", "project_header", "header"}:
                    continue
                if ntype == "system_header" or str(node).startswith("//system:"):
                    continue
                if has_part_of(node):
                    continue
                tgt = best_dir_match(node)
                if tgt:
                    e = Edge(
                        node,
                        tgt,
                        parser_name="projection",
                        kind="affects",
                        derived_from="fallback:nearest_dir",
                        confidence=0.5,
                        fallback_attached=True,
                        fallback_rule="nearest_dir",
                    )
                    self.add_edge(e)
        if fuse_evidence:
            self.fuse_projection_evidence()

    def remove_edge(self, edge_index: EdgeIndex):
        """
        remove a edge from the graph.

        Args:
            edge_index (EdgeIndex): the edge index that need to be removed from the graph.
        """
        self.graph.remove_edge(*edge_index)

    def add_node(self, vertex: Vertex):
        """
        add a node to the graph.

        Args:
            vertex (Vertex): the node object that need to be added to the graph.
        """
        self.graph.add_node(**vertex)

    def get_node(self, node: Vertex) -> MutableMapping | None:
        """
        get the node object from the graph.

        Args:
            node (Vertex): the node object that need to be get from the graph.

        Returns:
            node: the node object (Networkx NodeView) that get from the graph.
        """
        return self.graph.nodes.get(node.label)

    def get_edge(self, edge: Edge) -> list[EdgeIndex]:
        """
        get the edge object from the graph.

        Args:
            edge (Edge): the edge object that need to be get from the graph.

        Returns:
            edges: the edge index list that get from the graph.
        """

        return self.query_edge_by_label(**edge)

    def get_edge_data(self, edge_index: EdgeIndex) -> Mapping:
        """Return edge attribute mapping by index.

        Args:
            edge_index (EdgeIndex): Edge index tuple (u, v, key).

        Returns:
            Mapping: Edge data mapping from the underlying graph.
        """
        if edge_index[2] == -1:
            edge_index = (edge_index[0], edge_index[1], None)

        return self.graph.get_edge_data(*edge_index)

    def fuse_projection_evidence(self, kinds: Optional[set[str]] = None) -> None:
        """Fuse multi-evidence projection edges per (u,v,kind).

        This collapses multiple 'projection' edges between the same endpoints and
        of the same semantic kind into a single edge that merges confidences and
        provenance. It preserves non-projection edges untouched.

        Args:
            kinds (Optional[set[str]]): Which kinds to fuse; defaults to {'part_of','affects'}.

        Returns:
            None
        """
        if kinds is None:
            kinds = {"part_of", "affects"}

        groups: dict[tuple[str, str, str], list[tuple[str, str, int, Mapping]]] = (
            defaultdict(list)
        )
        for u, v, key, data in self.graph.edges(data=True, keys=True):
            if not isinstance(data, Mapping):
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
            derived_from: set[str] = set()
            fallback_any = False
            rules: set[str] = set()
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
                    self.remove_edge((u, v, key))
                except Exception:
                    continue
            # add a fused edge
            fused = Edge(
                u, v, parser_name="projection", kind=k, confidence=max_conf or None
            )
            if derived_from:
                fused["derived_from"] = sorted(derived_from)
            if fallback_any:
                fused["fallback_attached"] = True
            if rules:
                fused["fallback_rule"] = sorted(rules)
            self.add_edge(fused)

    def get_node_data(self, node_label: str) -> Optional[Mapping]:
        """Return node attribute mapping by label.

        Args:
            node_label (str): Node label.

        Returns:
            Optional[Mapping]: Node data mapping or None.
        """
        return self.graph.nodes.get(node_label)

    def get_predecessors_of_type(self, node_label: str, edge_type: str):
        """Return predecessors connected by a semantic edge type.

        This matches against 'kind' then 'label' then 'type' for robustness.

        Args:
            node_label (str): Target node label.
            edge_type (str): Semantic edge type to match.

        Returns:
            list[str]: Predecessor node labels.
        """

        def ek(d: Mapping) -> Optional[str]:
            return d.get("kind") or d.get("label") or d.get("type")

        return [
            u
            for u, v, data in self.graph.in_edges(node_label, data=True)
            if ek(data) == edge_type
        ]

    def edge_subgraph(self, edges: list[EdgeIndex]) -> "GraphManager":
        """
        edge subgraph of the graph.

        Args:
            edges (list[EdgeIndex]): the edge index list that need to be subgraph.

        Returns:
            GraphManager: the subgraph of the graph.
        """
        new_graph = GraphManager()
        new_graph.graph = self.graph.edge_subgraph(edges)
        return new_graph

    def node_subgraph(self, nodes: list[str]) -> "GraphManager":
        """
        node subgraph of the graph.

        Args:
            nodes (list[str]): the node label list that need to be subgraph.

        Returns:
            GraphManager: the subgraph of the graph.
        """
        new_graph = GraphManager()
        new_graph.graph = self.graph.subgraph(nodes)
        return new_graph

    def query_node_by_label(self, label: str) -> MutableMapping | None:
        """
        get the node object from the graph.

        Args:
            label (str): the label of the node.

        Returns:
            node: the node object (networkx NodeView) that get from the graph.
        """
        return self.graph.nodes.get(label)

    def query_edge_by_label(
        self, u_for_edge: str, v_for_edge: str, key=-1, **kwargs
    ) -> list[EdgeIndex]:
        """
        get the edge object from the graph.

        attention: only when the length of return list is zero, the edge is not in the graph.

        Args:
            u_for_edge (str): the source node (label) of the edge.
            v_for_edge (str): the target node (label) of the edge.
            key (int): the key of the edge.
            **kwargs: the other parameters of the edge.

        Returns:
            edge_index: the edge index that get from the graph.
        """
        edge_dict = self.graph.get_edge_data(
            u_for_edge, v_for_edge, None if key == -1 else key
        )

        if edge_dict is None:
            return []

        kwargs = {
            k: tuple(v) if isinstance(v, list) else v
            for k, v in kwargs.items()
            if k not in self._edge_keys_to_exclude
        }

        # ! get_edge_data return both multiple edges and single edge in dict.
        # * so we need to check the type of the key in the dict.

        if all(isinstance(key, str) for key in edge_dict.keys()):
            return [
                (u_for_edge, v_for_edge, key)
                for _ in filter(
                    lambda x: self._compare_edge(x, kwargs), edge_dict.keys()
                )
            ]

        if all(isinstance(key, int) for key in edge_dict.keys()):
            return [
                (u_for_edge, v_for_edge, item[0])
                for item in filter(
                    lambda x: self._compare_edge(x[1], kwargs), edge_dict.items()
                )
            ]

        raise ValueError("The edge key is not consistent in the graph.")

    def _compare_edge(self, target: dict, edge_property_dict: dict) -> bool:
        """
        helper function to compare the edge object and the edge property dict.

        Args:
            target (dict): the edge object.
            edge_property_dict (dict): the edge property dict.

        Returns:
            bool: the result of the comparison.
        """
        if not target and not edge_property_dict:
            return True

        keys_to_exclude = {"u_for_edge", "v_for_edge", "key"}
        new_d = {
            k: tuple(v) if isinstance(v, list) else v
            for k, v in target.items()
            if k not in keys_to_exclude
        }

        edge_dict = {
            k: tuple(v) if isinstance(v, list) else v
            for k, v in edge_property_dict.items()
        }

        return set(set(edge_dict.items())).issubset(new_d.items())

    def _compare_node(self, target: dict, node_property_dict: dict) -> bool:
        """
        helper function to compare the node object and the node property dict.

        Args:
            target (dict): the node object.
            node_property_dict (dict): the node property dict.

        Returns:
            bool: the result of the comparison.
        """

        if not target and not node_property_dict:
            return True

        keys_to_exclude = {"node_for_adding"}
        new_d = {
            k: tuple(v) if isinstance(v, list) else v
            for k, v in target.items()
            if k not in keys_to_exclude
        }
        node_dict = {
            k: tuple(v) if isinstance(v, list) else v
            for k, v in node_property_dict.items()
        }
        return set(set(node_dict.items())).issubset(new_d.items())

    def filter_edges(self, **kwargs) -> Iterator[tuple[EdgeIndex, dict]]:
        """
        filter the edges in the graph by the edge property dict.

        Usage:
            ```python
            for edge_index, edge_data in graph.filter_edges(type="dependency"):
                graph.remove_edge(edge_index)
                ...
            ```

        Args:
            **kwargs: the edge property dict used to filter the edges.

        Returns:
            Iterator[EdgeIndex, Data]: the edge separated by the edge index and the edge data.
        """
        for edge in filter(
            lambda x: self._compare_edge(x[3], kwargs),
            self.graph.edges(data=True, keys=True),
        ):
            yield (edge[0], edge[1], edge[2]), edge[3]

    def filter_nodes(self, **kwargs) -> Iterator[tuple[str, dict]]:
        """
        filter the nodes in the graph by the node property dict.

        Usage:
            ```python
            for node_index, node_data in graph.filter_nodes(type="package"):
                print(node_index, node_data)
            ```

        Args:
            **kwargs: the node property dict used to filter the nodes.

        Returns:
            Iterator[str, Data]: the node separated by the node label and the node data.
        """
        for node in filter(
            lambda x: self._compare_node(x[1], kwargs), self.graph.nodes(data=True)
        ):
            yield node[0], node[1]

    def modify_node_attribute(
        self, node_label: str, new_attribute: str, new_value: Any
    ) -> bool:
        """
        Modify the attribute of a node in the graph.

        Args:
            graph_manager (GraphManager): The graph manager object containing the graph.
            node_label (str): The label of the node to be modified.
            new_attribute (str): The name of the new attribute to be added or modified.
            new_value (Any): The value of the new attribute.

        Returns:
            bool: True if the node was found and modified, False otherwise.
        """
        target_node = self.query_node_by_label(node_label)
        if target_node:
            target_node[new_attribute] = new_value
            nx.set_node_attributes(self.graph, {node_label: target_node})

            return True
        else:
            return False

    def get_subgraph_depth(
        self, start_node: Optional[str] = None, depth=2, leaf_flag=True
    ):
        """
        Get a subgraph with a specified depth from the start node.

        Args:
            start_node (str): The label of the start node.
            depth (int): The depth of the subgraph.
            leaf_flag (bool): The start_node type.

        Returns:
            GraphManager: The subgraph with the specified depth.
        """
        if leaf_flag is False:
            nodes_to_visit: list[str] = [start_node] if start_node else self.root_nodes
            visited_nodes = set()
            current_depth = 0

            while nodes_to_visit and current_depth < depth:
                next_nodes = []
                for node in nodes_to_visit:
                    if node not in visited_nodes:
                        visited_nodes.add(node)
                        next_nodes.extend(self.successors(node))
                nodes_to_visit = next_nodes
                current_depth += 1
            # print(visited_nodes)
            new_graph = GraphManager()
            new_graph.graph = self.graph.subgraph(visited_nodes)
            return new_graph
        else:
            leaf_nodes = []
            for node in self.graph.nodes:
                if self.graph.out_degree(node) == 0:
                    n = self.query_node_by_label(node)
                    if n and n["type"] == "code":
                        leaf_nodes.append(node)
            context_list = []
            for leaf in leaf_nodes:
                ancestors = self.get_ancestors(leaf, 2)
                if ancestors:
                    nodes_to_visit = [ancestors[0]]
                    visited_nodes = set()
                    current_depth = 0

                    while nodes_to_visit and current_depth < depth:
                        next_nodes = []
                        for node in nodes_to_visit:
                            if node not in visited_nodes:
                                visited_nodes.add(node)
                                next_nodes.extend(self.graph.successors(node))
                        nodes_to_visit = next_nodes
                        current_depth += 1
                    subgraph = GraphManager()
                    subgraph.graph = self.graph.subgraph(visited_nodes)
                    context_list.append(subgraph)
            return context_list

    def get_sibling_pairs(self) -> list[tuple[str, str]]:
        """
        Find all pairs of sibling nodes (nodes with the same parent) in the graph.

        Returns:
            list[tuple[str, str]]: A list of tuples where each tuple contains two sibling node labels.
        """
        sibling_pairs = []
        parent_to_children = defaultdict(list)

        # Build a dictionary mapping parents to their children
        for node in self.graph.nodes:
            for pred in self.graph.predecessors(node):
                parent_to_children[pred].append(node)

        # Find sibling pairs
        for children in parent_to_children.values():
            if len(children) > 1:
                for idx, child in enumerate(children):
                    for sibling in children[idx + 1 :]:
                        sibling_pairs.append((child, sibling))

        return sibling_pairs

    def save(self, file_path: str, stringizer=None, save_format="json"):
        """save the graph to the file."""
        if save_format == "gml":
            nx.write_gml(
                self.graph, file_path, stringizer=stringizer if stringizer else str
            )
        elif save_format == "json":
            data = nx.readwrite.json_graph.node_link_data(self.graph, edges="edges")
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, indent=4, ensure_ascii=False))
        else:
            raise ValueError(
                f"Unsupported save_format: {save_format}. Supported formats are 'gml' and 'json'."
            )

    def save_with_mapping(
        self,
        file_path: str,
        *,
        type_mapping: Optional[dict[str, str]] = None,
        unify_edge_label: Optional[str] = "dep",
        stringizer=None,
        save_format: str = "json",
    ) -> None:
        """
        Save a transformed copy of the graph:
        - map node "type" using type_mapping (e.g. system_header->code, project_header->code)
        - set every edge's label to unify_edge_label (if provided)
        """

        # Default mapping
        if type_mapping is None:
            type_mapping = {
                "system_header": "code",
                "project_header": "code",
                # keep these unchanged explicitly (document intent)
                "code": "code",
                "shared_library": "shared_library",
                "static_library": "static_library",
            }

        transformed = nx.MultiDiGraph()

        # Copy nodes with transformed type
        for node_label, data in self.graph.nodes(data=True):
            new_data = dict(data) if data else {}
            node_type = new_data.get("type")
            if node_type in type_mapping:
                new_data["type"] = type_mapping[node_type]
            # else: keep as-is for other types
            transformed.add_node(node_label, **new_data)

        # Copy edges, unify label but preserve original semantics in 'kind'
        for u, v, _key, data in self.graph.edges(keys=True, data=True):
            new_data = dict(data) if data else {}
            # Preserve original semantic edge type into a stable 'kind' field
            # Priority: existing 'kind' > 'label' > 'type'
            orig_kind = None
            if isinstance(data, dict):
                orig_kind = data.get("kind") or data.get("label") or data.get("type")
            if orig_kind is not None:
                new_data["kind"] = orig_kind

            # Optionally unify outward-facing label for downstream tools
            if unify_edge_label is not None:
                new_data["label"] = unify_edge_label

            transformed.add_edge(u, v, **new_data)

        # Persist
        if save_format == "gml":
            nx.write_gml(
                transformed, file_path, stringizer=stringizer if stringizer else str
            )
        elif save_format == "json":
            data_json = nx.readwrite.json_graph.node_link_data(
                transformed, edges="edges"
            )
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(data_json, indent=4, ensure_ascii=False))
        else:
            raise ValueError(
                f"Unsupported save_format: {save_format}. Supported formats are 'gml' and 'json'."
            )

    @classmethod
    def load_from_disk(cls, file_path: str):
        """load the graph from the file."""
        return cls(file_path)

    @classmethod
    def from_dict(cls, data: dict) -> "GraphManager":
        """
        Create a GraphManager instance from a dictionary.

        Args:
            data (dict): A dictionary containing graph data in node-link format.
                Expected keys: 'directed', 'multigraph', 'graph', 'nodes', 'edges'.

        Returns:
            GraphManager: A new GraphManager instance with the graph loaded from the dict.
        """
        instance = cls()
        instance.graph = nx.readwrite.json_graph.node_link_graph(data, edges="edges")
        return instance

    @classmethod
    def from_web_export(cls, web_export: dict[str, str]) -> "GraphManager":
        """
        Reconstruct a GraphManager from web export format.

        This method is designed to work seamlessly with the output from export_for_web().
        It decompresses and merges the skeleton and metadata to reconstruct the full graph.

        Args:
            web_export (dict): A dictionary containing compressed graph data:
                - 'skeleton': Compressed graph structure (base64 encoded)
                - 'node_metadata': Compressed node attributes (base64 encoded)
                - 'edge_metadata': Compressed edge attributes (base64 encoded)

        Returns:
            GraphManager: A new GraphManager instance with the full graph reconstructed.

        Example:
            >>> # On frontend: decompress, send to backend or reconstruct locally
            >>> # On backend:
            >>> graph = GraphManager.from_web_export(web_export)
            >>> # graph is now a fully reconstructed GraphManager instance
        """

        def decompress_and_decode(compressed_str: str) -> dict:
            """Decompress base64 gzipped string to dict"""
            compressed_bytes = base64.b64decode(compressed_str.encode("ascii"))
            json_bytes = gzip.decompress(compressed_bytes)
            json_str = json_bytes.decode("utf-8")
            return json.loads(json_str)

        # Decompress all parts
        skeleton = decompress_and_decode(web_export["skeleton"])
        node_metadata = decompress_and_decode(web_export["node_metadata"])
        edge_metadata = decompress_and_decode(web_export["edge_metadata"])

        # Create graph instance
        instance = cls()

        # Determine graph type
        if skeleton["directed"] and skeleton["multigraph"]:
            instance.graph = nx.MultiDiGraph()
        elif skeleton["directed"]:
            instance.graph = nx.DiGraph()
        elif skeleton["multigraph"]:
            instance.graph = nx.MultiGraph()
        else:
            instance.graph = nx.Graph()

        # Add nodes with metadata
        for node_info in skeleton["nodes"]:
            node_id = node_info["id"]
            attrs = node_metadata.get(node_id, {})
            instance.graph.add_node(node_id, **attrs)

        # Add edges with metadata
        for edge_info in skeleton["edges"]:
            u = edge_info["source"]
            v = edge_info["target"]
            key = edge_info["key"]
            edge_key = f"{u}_{v}_{key}"
            attrs = edge_metadata.get(edge_key, {})

            if skeleton["multigraph"]:
                instance.graph.add_edge(u, v, key=key, **attrs)
            else:
                instance.graph.add_edge(u, v, **attrs)

        return instance

    def create_vertex(self, label: str, **kwargs: Any) -> Vertex:
        """Create a vertex"""
        if "parser_name" not in kwargs:
            kwargs["parser_name"] = self.__class__.__name__
        return Vertex(label, **kwargs)

    def create_edge(self, u: str, v: str, **kwargs: Any) -> Edge:
        """Create an edge between two vertices"""
        if "parser_name" not in kwargs:
            kwargs["parser_name"] = self.__class__.__name__
        return Edge(u, v, **kwargs)

    def export_for_web(self) -> dict[str, str]:
        """
        Export graph for web scenarios with separate skeleton and metadata.

        This method exports the graph in two parts:
        1. Skeleton: Basic structure (node IDs and edge connections)
        2. Metadata: Node and edge attributes

        Both parts are JSON serialized, GZIP compressed, and base64 encoded
        for efficient transmission to web clients.

        Returns:
            dict: A dictionary containing:
                - 'skeleton': Compressed graph structure (base64 encoded)
                - 'node_metadata': Compressed node attributes (base64 encoded)
                - 'edge_metadata': Compressed edge attributes (base64 encoded)

        Example:
            >>> graph = GraphManager()
            >>> # ... build your graph ...
            >>> web_export = graph.export_for_web()
            >>> # Send web_export to frontend
            >>> # Frontend can decompress and merge to reconstruct full graph
        """
        # Extract skeleton (structure only)
        skeleton = {
            "directed": self.graph.is_directed(),
            "multigraph": self.graph.is_multigraph(),
            "nodes": [{"id": node} for node in self.graph.nodes()],
            "edges": [
                {"source": u, "target": v, "key": key}
                for u, v, key in self.graph.edges(keys=True)
            ],
        }

        # Extract node metadata
        node_metadata = {
            node: {k: v for k, v in data.items()}
            for node, data in self.graph.nodes(data=True)
        }

        # Extract edge metadata
        edge_metadata = {
            f"{u}_{v}_{key}": {k: v for k, v in data.items()}
            for u, v, key, data in self.graph.edges(keys=True, data=True)
        }

        # Compress and encode
        def compress_and_encode(data: dict) -> str:
            """Compress dict to gzipped base64 string"""
            json_str = json.dumps(data, ensure_ascii=False)
            json_bytes = json_str.encode("utf-8")
            compressed = gzip.compress(json_bytes, compresslevel=9)
            return base64.b64encode(compressed).decode("ascii")

        return {
            "skeleton": compress_and_encode(skeleton),
            "node_metadata": compress_and_encode(node_metadata),
            "edge_metadata": compress_and_encode(edge_metadata),
        }
