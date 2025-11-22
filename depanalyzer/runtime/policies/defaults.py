"""Default policy implementations for runtime extension points.

This module adapts existing Transaction and GraphManager behavior to
the public policy interfaces so that callers can override them
without changing CLI semantics.
"""

# Default policies keep running even when hooks raise unexpected exceptions.


from __future__ import annotations

from typing import Any, Dict

from depanalyzer.graph import EdgeKind, EdgeSpec, LinkClass, NodeSpec, NodeType
from depanalyzer.runtime.policies.protocols import (
    AssetProjectionPolicy,
    CodeDependencyContext,
    CodeDependencyMapper,
    ProjectionContext,
)


class DefaultAssetProjectionPolicy(AssetProjectionPolicy):
    """Default asset→artifact projection policy.

    This implementation simply forwards to the existing
    ``GraphManager.derive_asset_artifact_projection`` method using the
    provided ``ProjectionConfig``.
    """

    def project(self, ctx: ProjectionContext) -> None:
        """Execute projection using the GraphManager's built-in method.

        Args:
            ctx: ProjectionContext with graph and configuration.
        """
        ctx.graph.derive_asset_artifact_projection(config=ctx.projection_config)


class DefaultCodeDependencyMapper(CodeDependencyMapper):
    """Default code dependency mapper.

    This implementation preserves the behavior of
    ``Transaction._process_code_parse_result`` by mapping parsed
    includes/imports into graph nodes and edges for the supported
    ecosystems (currently C/C++ and Hvigor/ArkTS).
    """

    def map(self, ctx: CodeDependencyContext) -> None:
        """Map a single code parse result into the transaction graph.

        Args:
            ctx: CodeDependencyContext describing the parsed file.
        """
        tx = ctx.transaction_ctx
        graph = tx.graph
        if graph is None:
            return

        file_path = ctx.file_path
        parse_result: Dict[str, Any] = ctx.parse_result

        parser_name = parse_result.get("parser_name") or "code_parser"

        # Normalize file node ID.
        try:
            if graph.root_path:
                file_node_id = graph.normalize_path(file_path)
            else:
                file_node_id = str(file_path)
        except AttributeError:
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

        # Fallback behavior: create INCLUDE edges by path only.
        for include_path in includes:
            include_node_id = str(include_path)
            edge_spec = EdgeSpec(
                source=file_node_id,
                target=include_node_id,
                kind=EdgeKind.INCLUDE,
                parser_name=parser_name,
                attrs={
                    "path": include_node_id,
                    "link_class": LinkClass.SEMANTIC.value,
                },
            )
            graph.add_edge_spec(edge_spec)


DefaultAssetProjectionStrategy = DefaultAssetProjectionPolicy

__all__ = [
    "DefaultAssetProjectionPolicy",
    "DefaultAssetProjectionStrategy",
    "DefaultCodeDependencyMapper",
]
