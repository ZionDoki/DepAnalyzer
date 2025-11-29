"""Hvigor/ArkTS-specific code dependency mapper.

This module contains the default ``CodeDependencyMapper`` implementation
for the ``hvigor`` ecosystem. It maps ArkTS/TS/JS imports into CODE nodes
and IMPORT edges in the dependency graph.
"""

# Mapper intentionally guards against parser crashes to keep transactions running.


from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from depanalyzer.graph import LinkClass
from depanalyzer.graph import EdgeKind, GraphManager, NodeType
from depanalyzer.graph import EdgeSpec, NodeSpec
from depanalyzer.parsers.base import BaseCodeDependencyMapper
from depanalyzer.runtime.context import TransactionContext


class HvigorCodeDependencyMapper(BaseCodeDependencyMapper):
    """Default Hvigor/ArkTS import mapping strategy."""

    NAME: str = "hvigor_code_mapper"
    ECOSYSTEM: str = "hvigor"

    def _map_for_file(
        self,
        transaction_ctx: TransactionContext,
        graph: GraphManager,
        file_path: Path,
        parse_result: Dict[str, Any],
    ) -> None:
        """Map ArkTS/TS/JS imports to code nodes and IMPORT edges."""
        parser_name = parse_result.get("parser_name") or "hvigor_code"

        # Normalize file node ID.
        try:
            if graph.root_path:
                file_node_id = graph.normalize_path(file_path)
            else:
                file_node_id = str(file_path)
        except Exception:
            file_node_id = str(file_path)

        if not graph.has_node(file_node_id):
            file_spec = NodeSpec(
                id=file_node_id,
                type=NodeType.CODE,
                src_path=str(file_path.resolve()),
                path=str(file_path),
                name=file_path.name,
                parser_name=parser_name,
            )
            graph.add_node_spec(file_spec)

        includes = parse_result.get("includes")
        if includes is None:
            includes = parse_result.get("imports", [])

        for include_path in includes:
            if isinstance(include_path, Path):
                target_path = include_path
                target_path_str = str(include_path)
            else:
                target_path = Path(str(include_path))
                target_path_str = str(include_path)

            try:
                if graph.root_path and target_path.is_absolute():
                    include_node_id = graph.normalize_path(target_path)
                elif graph.root_path:
                    include_node_id = graph.normalize_path(
                        (file_path.parent / target_path).resolve()
                    )
                else:
                    include_node_id = target_path_str
            except Exception:
                include_node_id = target_path_str

            if not graph.has_node(include_node_id):
                include_spec = NodeSpec(
                    id=include_node_id,
                    type=NodeType.CODE,
                    path=target_path_str,
                    src_path=(
                        str(target_path.resolve())
                        if target_path.exists()
                        else target_path_str
                    ),
                    name=target_path.name,
                    parser_name=parser_name,
                )
                graph.add_node_spec(include_spec)

            edge_spec = EdgeSpec(
                source=file_node_id,
                target=include_node_id,
                kind=EdgeKind.IMPORT,
                parser_name=parser_name,
                attrs={"link_class": LinkClass.SEMANTIC.value},
            )
            graph.add_edge_spec(edge_spec)

        # Link unresolved imports to known dependency/package nodes so
        # code-level references connect to external modules.
        unresolved = parse_result.get("unresolved") or []
        if unresolved:
            dep_targets: Dict[str, str] = {}
            for node_id, attrs in graph.nodes():
                ntype = attrs.get("type")
                if ntype not in {
                    NodeType.EXTERNAL_LIBRARY.value,
                    NodeType.MODULE.value,
                    NodeType.PROXY.value,
                    NodeType.SHARED_LIBRARY.value,
                }:
                    continue
                name = attrs.get("name") or ""
                if name:
                    dep_targets.setdefault(name, node_id)
                # Also index by cleaned id fragments for best-effort matching
                dep_targets.setdefault(str(node_id).split("@", 1)[0].split(":", 1)[-1], node_id)

            for spec in unresolved:
                try:
                    raw = str(spec).strip().strip("\"'")
                except Exception:
                    continue
                if not raw:
                    continue
                candidate = raw.lstrip("@")
                base = candidate.split("/")[0] if "/" in candidate else candidate
                target_node = dep_targets.get(raw) or dep_targets.get(candidate) or dep_targets.get(base)
                if not target_node:
                    continue
                graph.add_edge_spec(
                    EdgeSpec(
                        source=file_node_id,
                        target=target_node,
                        kind=EdgeKind.IMPORT,
                        parser_name=parser_name,
                        attrs={
                            "link_class": LinkClass.SEMANTIC.value,
                            "derived_from": "hvigor_unresolved_import",
                        },
                    )
                )


__all__ = ["HvigorCodeDependencyMapper"]
