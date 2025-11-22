"""Public graph API surface."""

from depanalyzer.graph.core import EdgeKind, GraphBackend, GraphManager, NodeType
from depanalyzer.graph.models import (
    EdgeSpec,
    LinkClass,
    NodeSpec,
    canonicalize_edge,
    canonicalize_node,
    canonicalize_normalized_id,
    make_external_id,
    make_file_id,
    make_include_placeholder_id,
    make_system_header_id,
    normalize_node_id,
    validate_edge,
    validate_node,
)
from depanalyzer.graph.ops import (
    CondensationResult,
    GlobalDAG,
    GraphLike,
    build_condensation_dag,
    derive_asset_artifact_projection,
    fuse_projection_evidence,
    merge_graph_with_dependencies,
)
from depanalyzer.graph.projection_config import ProjectionConfig
from depanalyzer.graph.registry import GraphRegistry

__all__ = [
    "CondensationResult",
    "EdgeKind",
    "EdgeSpec",
    "GlobalDAG",
    "GraphBackend",
    "GraphLike",
    "GraphManager",
    "GraphRegistry",
    "LinkClass",
    "NodeSpec",
    "NodeType",
    "ProjectionConfig",
    "build_condensation_dag",
    "canonicalize_edge",
    "canonicalize_node",
    "canonicalize_normalized_id",
    "derive_asset_artifact_projection",
    "fuse_projection_evidence",
    "make_external_id",
    "make_file_id",
    "make_include_placeholder_id",
    "make_system_header_id",
    "merge_graph_with_dependencies",
    "normalize_node_id",
    "validate_edge",
    "validate_node",
]
