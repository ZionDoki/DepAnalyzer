"""Data models and identifiers used by the graph package."""

from .identifiers import (
    make_external_id,
    make_file_id,
    make_include_placeholder_id,
    make_system_header_id,
    normalize_node_id,
)
from .linking import LinkClass
from .schema import (
    EdgeKind,
    EdgeSpec,
    NodeSpec,
    NodeType,
    validate_edge,
    validate_node,
)
from .schema_utils import canonicalize_edge, canonicalize_node, canonicalize_normalized_id

__all__ = [
    "EdgeKind",
    "EdgeSpec",
    "LinkClass",
    "NodeSpec",
    "NodeType",
    "canonicalize_edge",
    "canonicalize_node",
    "canonicalize_normalized_id",
    "make_external_id",
    "make_file_id",
    "make_include_placeholder_id",
    "make_system_header_id",
    "normalize_node_id",
    "validate_edge",
    "validate_node",
]
