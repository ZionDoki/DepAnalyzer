"""Canonical schema helpers for nodes and edges.

This module centralizes common semantics used by parsers when creating graph
nodes and edges. It enforces a single source of truth for the semantic edge
type via the `kind` attribute while keeping `label` for forward compatibility.

Parsers should prefer these helpers to reduce divergence and avoid mixing
`label`/`type` semantics.
"""

from __future__ import annotations

from typing import Any, Dict


class EdgeKind:
    """Common semantic kinds for edges."""

    INCLUDE = "include"
    IMPORT = "import"
    SOURCES = "sources"
    LINK_LIBRARIES = "link_libraries"
    INCLUDE_DIRS = "include_dirs"
    DEPENDS_ON = "depends_on"
    CONTAINS = "contains"
    DEFINED_BY = "defined_by"
    ALIAS_OF = "alias_of"
    IMPLEMENTS_NATIVE = "implements_native"


class NodeType:
    """Common node type constants."""

    CODE = "code"
    SYSTEM_HEADER = "system_header"
    PROJECT_HEADER = "project_header"
    SHARED_LIBRARY = "shared_library"
    STATIC_LIBRARY = "static_library"
    EXECUTABLE = "executable"
    SUBDIRECTORY = "subdirectory"
    MODULE = "module"
    CONFIG = "config"
    EXTERNAL_LIBRARY = "external_library"


def edge_attrs(kind: str, parser_name: str, **extras: Any) -> Dict[str, Any]:
    """Return standardized edge attributes with `kind` and a compatible `label`.

    Args:
        kind: Semantic edge kind (e.g., "include", "sources").
        parser_name: Name of the parser producing this edge.
        **extras: Additional attributes to attach to the edge.

    Returns:
        A dictionary of edge attributes to pass into GraphManager.create_edge.
    """
    payload: Dict[str, Any] = {"kind": kind, "label": kind, "parser_name": parser_name}
    payload.update({k: v for k, v in extras.items() if v is not None})
    return payload


def node_attrs(node_type: str, parser_name: str, **extras: Any) -> Dict[str, Any]:
    """Return standardized node attributes with explicit `type`.

    Args:
        node_type: Node type string (e.g., "code").
        parser_name: Name of the parser producing this node.
        **extras: Additional attributes to attach to the node.

    Returns:
        A dictionary of node attributes to pass into GraphManager.create_vertex.
    """
    payload: Dict[str, Any] = {"type": node_type, "parser_name": parser_name}
    payload.update({k: v for k, v in extras.items() if v is not None})
    return payload

