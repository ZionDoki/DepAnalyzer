"""Projection helpers used by :class:`depanalyzer.graph.core.manager.GraphManager`."""

from __future__ import annotations

import os
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

import networkx as nx

from ..projection_config import ProjectionConfig

if TYPE_CHECKING:
    from ..core.manager import GraphManager


def _edge_kind(data: Dict[str, Any]) -> Optional[str]:
    """Resolve the semantic kind stored on an edge attribute mapping.

    Args:
        data: Edge attribute mapping.

    Returns:
        Optional[str]: Semantic kind extracted from ``kind``/``label``/``type``.
    """
    if not isinstance(data, dict):
        return None
    return data.get("kind") or data.get("label") or data.get("type")


def _safe_get_type(nodes_data: Dict[str, Any], node_id: str) -> Optional[str]:
    """Safely get node type from nodes data dict.

    Args:
        nodes_data: Dictionary mapping node IDs to node attribute dicts.
        node_id: The node ID to look up.

    Returns:
        Optional[str]: The node type, or None if not found or invalid.
    """
    node_data = nodes_data.get(node_id)
    if not isinstance(node_data, dict):
        return None
    return node_data.get("type")


def derive_asset_artifact_projection(
    manager: "GraphManager",
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
    """Derive asset→artifact projection edges on the manager's graph.

    Args:
        manager: Target graph manager.
        config: Optional base :class:`ProjectionConfig` to read defaults from.
        enable_fallback: Enable conservative fallbacks for unmatched assets.
        enable_link_closure: Propagate influence across link_libraries edges.
        enable_header_closure: Propagate include effects using multi-hop closure.
        max_header_hops: Max include hops for header closure.
        ignore_external_placeholders_in_fallback: Skip external placeholders in fallback.
        nearest_dir_min_evidence: Min source evidence to accept nearest-dir fallback.
        link_closure_max_hops: Max hops across artifact links during link closure.
        resolve_include_placeholders: Resolve placeholder include nodes to real headers.
        fuse_evidence: Fuse multi-evidence projection edges for compactness.
        fallback_max_targets_per_node: Cap fallback targets attached per asset.
        fallback_disable_import_inherited: Skip import/include inheritance fallback when True.
        fallback_disable_nearest_dir: Skip nearest-by-directory fallback when True.

    Returns:
        None
    """

    base_cfg = config or ProjectionConfig()

    eff_enable_fallback = (
        enable_fallback if enable_fallback is not None else base_cfg.enable_fallback
    )
    eff_enable_link_closure = (
        enable_link_closure
        if enable_link_closure is not None
        else base_cfg.enable_link_closure
    )
    eff_enable_header_closure = (
        enable_header_closure
        if enable_header_closure is not None
        else base_cfg.enable_header_closure
    )
    eff_max_header_hops = (
        max_header_hops if max_header_hops is not None else base_cfg.max_header_hops
    )
    eff_ignore_external_placeholders = (
        ignore_external_placeholders_in_fallback
        if ignore_external_placeholders_in_fallback is not None
        else base_cfg.ignore_external_placeholders_in_fallback
    )
    eff_nearest_dir_min_evidence = (
        nearest_dir_min_evidence
        if nearest_dir_min_evidence is not None
        else base_cfg.nearest_dir_min_evidence
    )
    eff_link_closure_max_hops = (
        link_closure_max_hops
        if link_closure_max_hops is not None
        else base_cfg.link_closure_max_hops
    )
    eff_resolve_include_placeholders = (
        resolve_include_placeholders
        if resolve_include_placeholders is not None
        else base_cfg.resolve_include_placeholders
    )
    eff_fuse_evidence = (
        fuse_evidence if fuse_evidence is not None else base_cfg.fuse_evidence
    )
    eff_fallback_max_targets_per_node = (
        fallback_max_targets_per_node
        if fallback_max_targets_per_node is not None
        else base_cfg.fallback_max_targets_per_node
    )
    eff_fallback_disable_import_inherited = (
        fallback_disable_import_inherited
        if fallback_disable_import_inherited is not None
        else base_cfg.fallback_disable_import_inherited
    )
    eff_fallback_disable_nearest_dir = (
        fallback_disable_nearest_dir
        if fallback_disable_nearest_dir is not None
        else base_cfg.fallback_disable_nearest_dir
    )

    subdirs = {}
    artifact_types = {
        "shared_library",
        "static_library",
        "executable",
        "module",
        "module_library",
        "interface_library",
        "object",
    }

    # Access the underlying graph for edge/node iteration
    graph = manager._backend.native_graph

    # Collect subdirs and artifacts in a single pass over nodes
    artifacts_to_process = []
    for node, nd in manager.nodes():
        ntype = nd.get("type")
        if ntype == "subdirectory":
            src_path = nd.get("src_path")
            if src_path:
                subdirs[node] = src_path.replace("\\", "/")
        elif ntype in artifact_types:
            target_src_path = nd.get("src_path")
            if target_src_path:
                artifacts_to_process.append((node, target_src_path.replace("\\", "/")))

    # Pre-sort subdirs by path length descending for early exit optimization
    sorted_subdirs = sorted(subdirs.items(), key=lambda x: len(x[1]), reverse=True)

    for node, target_src_path in artifacts_to_process:
        best_subdir = None
        # Since sorted by length descending, first match is the longest
        for subdir_id, subdir_path in sorted_subdirs:
            if target_src_path.startswith(subdir_path + "/"):
                best_subdir = subdir_id
                break  # Early exit - first match is longest due to sorting

        if best_subdir:
            manager.add_edge(
                best_subdir,
                node,
                edge_kind="contains",
                parser_name="projection",
                derived_from="subdirectory_inference",
                confidence=1.0,
            )

    part_of_added = set()
    for u, v, data in graph.edges(data=True):
        k = _edge_kind(data)
        if k == "sources":
            manager.add_edge(
                v,
                u,
                edge_kind="part_of",
                parser_name="projection",
                derived_from="sources",
                confidence=1.0,
            )
            part_of_added.add((v, u))
        elif k == "contains":
            manager.add_edge(
                v,
                u,
                edge_kind="part_of",
                parser_name="projection",
                derived_from="contains",
                confidence=1.0,
            )
            part_of_added.add((v, u))

    code_to_artifacts: Dict[str, Set[str]] = {}
    for x, y, data in graph.edges(data=True):
        if _edge_kind(data) == "part_of":
            code_to_artifacts.setdefault(x, set()).add(y)

    nodes_data_all = dict(graph.nodes(data=True))
    for u, v, data in graph.edges(data=True):
        if _edge_kind(data) == "include":
            code_node = u
            header_node = v
            header_type = _safe_get_type(nodes_data_all, header_node)
            if header_type == "system_header" or str(header_node).startswith(
                "//system:"
            ):
                continue
            for tgt in code_to_artifacts.get(code_node, set()):
                manager.add_edge(
                    header_node,
                    tgt,
                    edge_kind="affects",
                    parser_name="projection",
                    derived_from="include+part_of",
                    confidence=0.8,
                )

    if eff_enable_link_closure:
        rev_adj: Dict[str, Set[str]] = defaultdict(set)
        nodes_data = dict(graph.nodes(data=True))
        for u, v, data in graph.edges(data=True):
            src_type = _safe_get_type(nodes_data, u)
            tgt_type = _safe_get_type(nodes_data, v)
            if (
                _edge_kind(data) == "link_libraries"
                and src_type in artifact_types
            ):
                if tgt_type in artifact_types:
                    rev_adj[v].add(u)

        for code, bases in list(code_to_artifacts.items()):
            for base in list(bases):
                frontier = deque([(base, 0)])
                seen = {base}
                while frontier:
                    cur, d = frontier.popleft()
                    if d >= eff_link_closure_max_hops:
                        continue
                    for nxt in rev_adj.get(cur, set()):
                        if nxt in seen:
                            continue
                        seen.add(nxt)
                        conf = max(0.5, 0.7 - 0.1 * d)
                        manager.add_edge(
                            code,
                            nxt,
                            edge_kind="affects",
                            parser_name="projection",
                            derived_from="link_closure",
                            confidence=conf,
                            hops=d + 1,
                        )
                        frontier.append((nxt, d + 1))

    if eff_enable_header_closure and eff_max_header_hops > 0:
        inc_adj: Dict[str, Set[str]] = defaultdict(set)
        for u, v, data in graph.edges(data=True):
            if _edge_kind(data) == "include":
                inc_adj[u].add(v)

        node_to_artifacts: Dict[str, Set[str]] = defaultdict(set)
        for u, v, data in graph.edges(data=True):
            k = _edge_kind(data)
            if k in {"part_of", "affects"}:
                node_to_artifacts[u].add(v)

        for start, arts in list(node_to_artifacts.items()):
            if not arts:
                continue
            st_type = _safe_get_type(nodes_data_all, start)
            if st_type == "system_header" or str(start).startswith("//system:"):
                continue
            frontier = deque([(start, 0)])
            visited = {start}
            while frontier:
                cur, d = frontier.popleft()
                if d >= eff_max_header_hops:
                    continue
                for nxt in inc_adj.get(cur, set()):
                    if nxt in visited:
                        continue
                    visited.add(nxt)
                    ntype = _safe_get_type(nodes_data_all, nxt)
                    if not (
                        ntype == "system_header" or str(nxt).startswith("//system:")
                    ):
                        conf = max(0.5, 0.6 - 0.1 * d)
                        for tgt in arts:
                            manager.add_edge(
                                nxt,
                                tgt,
                                edge_kind="affects",
                                parser_name="projection",
                                derived_from="include_closure",
                                confidence=conf,
                                hops=d + 1,
                            )
                    frontier.append((nxt, d + 1))

    if eff_resolve_include_placeholders:
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
            if _edge_kind(data) != "include":
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
                            manager.add_edge(
                                cand,
                                tgt,
                                edge_kind="affects",
                                parser_name="projection",
                                derived_from="include_dir_resolve",
                                confidence=0.75,
                            )
                            break

    if not eff_enable_fallback:
        if eff_fuse_evidence:
            fuse_projection_evidence(manager)
        return

    artifact_dirs: Dict[str, str] = {}
    for node, nd in graph.nodes(data=True):
        if nd.get("type") in artifact_types:
            src_path = nd.get("src_path")
            if isinstance(src_path, str) and src_path:
                normalized = src_path.replace("\\", "/")
                artifact_dirs[node] = os.path.dirname(normalized)
            else:
                if isinstance(node, str) and node.startswith("//"):
                    path_part = node[2:].split(":")[0]
                    artifact_dirs[node] = (
                        path_part.rsplit("/", 1)[0]
                        if "/" in path_part
                        else path_part
                    )

    # Pre-sort artifact_dirs by directory length descending for early exit optimization
    # Also pre-normalize the directory paths to avoid repeated string operations
    sorted_artifact_dirs = sorted(
        [(tgt, tdir.replace("\\", "/").lstrip("/")) for tgt, tdir in artifact_dirs.items() if tdir],
        key=lambda x: len(x[1]),
        reverse=True,
    )

    def has_part_of(n: str) -> bool:
        for _, _, ed in graph.out_edges(n, data=True):
            if _edge_kind(ed) == "part_of":
                return True
        return False

    if not eff_fallback_disable_import_inherited:
        for node, nd in list(graph.nodes(data=True)):
            ntype = nd.get("type")
            if ntype not in {"code", "project_header", "header"}:
                continue
            if ntype == "system_header" or str(node).startswith("//system:"):
                continue
            if eff_ignore_external_placeholders:
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
                k = _edge_kind(ed)
                if k in {"import", "include"}:
                    for tgt in code_to_artifacts.get(pred, set()):
                        if tgt not in candidate_targets_seen:
                            candidate_targets_seen.add(tgt)
                            candidate_targets_list.append(tgt)
            if (
                isinstance(eff_fallback_max_targets_per_node, int)
                and eff_fallback_max_targets_per_node > 0
            ):
                candidate_targets_list = candidate_targets_list[
                    :eff_fallback_max_targets_per_node
                ]
            for tgt in candidate_targets_list:
                manager.add_edge(
                    node,
                    tgt,
                    edge_kind="affects",
                    parser_name="projection",
                    derived_from="fallback:import_inherited",
                    confidence=0.6,
                    fallback_attached=True,
                    fallback_rule="import_inherited",
                )

    def best_dir_match(node_label: str) -> Optional[str]:
        if not (isinstance(node_label, str) and node_label.startswith("//")):
            return None
        node_dir = node_label[2:]
        if ":" in node_dir:
            node_dir = node_dir.split(":")[0]
        if "/" in node_dir:
            node_dir = node_dir.rsplit("/", 1)[0]
        nd = node_dir.replace("\\", "/")

        # Use pre-sorted list (by length descending) for early exit
        for tgt, td in sorted_artifact_dirs:
            if nd.startswith(td):
                if eff_nearest_dir_min_evidence > 0:
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
                return tgt  # Early exit - first match is longest due to sorting
        return None

    if not eff_fallback_disable_nearest_dir:
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
                manager.add_edge(
                    node,
                    tgt,
                    edge_kind="affects",
                    parser_name="projection",
                    derived_from="fallback:nearest_dir",
                    confidence=0.5,
                    fallback_attached=True,
                    fallback_rule="nearest_dir",
                )
    if eff_fuse_evidence:
        fuse_projection_evidence(manager)


def fuse_projection_evidence(
    manager: "GraphManager", kinds: Optional[Set[str]] = None
) -> None:
    """Fuse multi-evidence projection edges per (u, v, kind).

    Args:
        manager: Target graph manager.
        kinds: Which kinds to fuse; defaults to {"part_of", "affects"}.

    Returns:
        None
    """

    if kinds is None:
        kinds = {"part_of", "affects"}

    groups: Dict[tuple[str, str, str], List[tuple[str, str, int, Dict[str, Any]]]] = (
        defaultdict(list)
    )

    for u, v, key, data in manager.edges():
        if not isinstance(data, dict):
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
            except (TypeError, ValueError):
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

        for _, _, key, _ in items:
            try:
                manager.remove_edge(u, v, key)
            except nx.NetworkXError:
                continue

        attrs = {"kind": k, "parser_name": "projection"}
        if max_conf:
            attrs["confidence"] = max_conf
        if derived_from:
            attrs["derived_from"] = sorted(derived_from)
        if fallback_any:
            attrs["fallback_attached"] = True
        if rules:
            attrs["fallback_rule"] = sorted(rules)
        manager.add_edge(u, v, edge_kind=k, **attrs)
