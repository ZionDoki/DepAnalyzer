"""C/C++-specific code dependency mapper.

This module contains the default ``CodeDependencyMapper`` implementation
for the ``cpp`` ecosystem. It maps include directives emitted by the
process-pool code parser into header nodes and INCLUDE edges using
CMake include_dirs and target membership as primary evidence.
"""

# Mapping logic intentionally tolerates parser failures on a per-file basis.


from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from depanalyzer.graph import (
    make_include_placeholder_id,
    make_system_header_id,
)
from depanalyzer.graph import LinkClass
from depanalyzer.graph import EdgeKind, GraphManager, NodeType
from depanalyzer.graph import EdgeSpec, NodeSpec
from depanalyzer.parsers.base import BaseCodeDependencyMapper
from depanalyzer.runtime.context import TransactionContext


class CppCodeDependencyMapper(BaseCodeDependencyMapper):
    """Default C/C++ include-to-header mapping strategy."""

    NAME: str = "cpp_code_mapper"
    ECOSYSTEM: str = "cpp"

    def _map_for_file(
        self,
        transaction_ctx: TransactionContext,
        graph: GraphManager,
        file_path: Path,
        parse_result: Dict[str, Any],
    ) -> None:
        """Map C/C++ includes to header nodes and INCLUDE edges."""
        parser_name = parse_result.get("parser_name") or "cpp_code"

        # Normalize file node ID to //relative form when possible.
        try:
            if graph.root_path:
                file_node_id = graph.normalize_path(file_path)
            else:
                file_node_id = str(file_path)
        except Exception:
            file_node_id = str(file_path)

        # Ensure file node exists as a CODE node using the structured schema.
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

        system_headers = {
            # C standard headers
            "stdio.h",
            "stdlib.h",
            "string.h",
            "stdint.h",
            "unistd.h",
            "fcntl.h",
            "sys/stat.h",
            "errno.h",
            # C++ standard library headers commonly seen in native code
            "cstring",
            "cstdint",
        }

        # Discover owning targets and their include_dirs from the skeleton graph.
        owning_targets: list[str] = []
        include_dirs_for_targets: dict[str, list[str]] = {}
        native_graph = graph.backend.native_graph 
        nodes_data = dict(native_graph.nodes(data=True))

        for u, _, ed in native_graph.in_edges(file_node_id, data=True):
            kind = ed.get("kind") or ed.get("label") or ed.get("type")
            if kind == EdgeKind.SOURCES.value or kind == "sources":
                owning_targets.append(u)

        for tgt in owning_targets:
            nd = nodes_data.get(tgt, {}) or {}
            incs = nd.get("include_dirs") or []
            if isinstance(incs, list):
                include_dirs_for_targets[tgt] = [
                    i.replace("\\", "/").lstrip("/") for i in incs if isinstance(i, str)
                ]

        repo_root = getattr(transaction_ctx.workspace, "root_path", None)

        for include_path in includes:
            include_str = (
                str(include_path).replace("\\", "/")
                if not isinstance(include_path, Path)
                else str(include_path).replace("\\", "/")
            )

            # 1) System headers (independent of build config).
            if include_str.startswith("/") or include_str in system_headers:
                header_id = make_system_header_id(include_str)
                header_type = NodeType.SYSTEM_HEADER
                header_src: Path | None = None
            else:
                header_id = None
                header_type = NodeType.CODE
                header_src = None

                # 2) Language-level relative includes: resolve against source dir.
                try:
                    candidate = (file_path.parent / include_str).resolve()
                except (OSError, ValueError):
                    candidate = None

                if candidate and candidate.is_file():
                    header_src = candidate
                else:
                    # 3) Use CMake include_dirs as the primary configuration-driven
                    # search space for project headers.
                    if isinstance(repo_root, Path):
                        for tgt, inc_dirs in include_dirs_for_targets.items():
                            for inc in inc_dirs:
                                base_dir = (repo_root / inc).resolve()
                                cand = (base_dir / include_str).resolve()
                                if cand.is_file():
                                    header_src = cand
                                    break
                            if header_src:
                                break

                # 4) Still not resolved: fall back to a project-relative
                # include placeholder and let later projection logic decide.
                if header_src is None:
                    header_id = make_include_placeholder_id(include_str)
                    header_type = NodeType.PROJECT_HEADER

            # Derive header_id for resolved headers when src_path is known.
            if header_id is None and header_src is not None:
                try:
                    if graph.root_path:
                        header_id = graph.normalize_path(header_src)
                    else:
                        header_id = str(header_src)
                except Exception:
                    header_id = str(header_src)

            # Ensure header node exists with proper type.
            if not graph.has_node(header_id):
                header_spec = NodeSpec(
                    id=header_id,
                    type=header_type,
                    src_path=str(header_src) if header_src is not None else None,
                    name=include_str.split("/")[-1],
                    parser_name=parser_name,
                )
                graph.add_node_spec(header_spec)

            # Connect code file -> header with semantic "include" edge.
            edge_spec = EdgeSpec(
                source=file_node_id,
                target=header_id,
                kind=EdgeKind.INCLUDE,
                parser_name=parser_name,
                attrs={"link_class": LinkClass.SEMANTIC.value},
            )
            graph.add_edge_spec(edge_spec)


__all__ = ["CppCodeDependencyMapper"]
