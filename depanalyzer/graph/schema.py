"""Canonical graph schema models and validation.

This module defines a single source of truth for node/edge types and
their basic structure. All graph construction code should prefer the
NodeSpec/EdgeSpec helpers (with Pydantic validation) instead of
assembling ad-hoc dictionaries.
"""

from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from depanalyzer.utils.path_utils import normalize_node_id as _normalize_path_id

logger = logging.getLogger("depanalyzer.graph.schema")


class NodeType(str, Enum):
    """Node type constants for the unified graph schema."""

    # Core semantic types
    CODE = "code"
    SYSTEM_HEADER = "system_header"
    PROJECT_HEADER = "project_header"

    CONFIG = "config"
    MODULE = "module"
    EXTERNAL_LIBRARY = "external_library"
    EXTERNAL_DEP = "external_dep"
    PROCESS = "process"
    TARGET = "target"
    ARTIFACT = "artifact"
    BUILD_CONFIG = "build_config"
    SUBDIRECTORY = "subdirectory"
    TOOLCHAIN = "toolchain"
    PROXY = "proxy"

    # Legacy / transitional types kept for compatibility
    HEADER = "header"
    SHARED_LIBRARY = "shared_library"
    STATIC_LIBRARY = "static_library"
    EXECUTABLE = "executable"
    ASSET = "asset"

    # OpenHarmony packaging types
    HAP = "hap"
    HAR = "har"
    HSP = "hsp"

    # Fallback for places that still don't set a concrete type.
    UNKNOWN = "unknown"


class EdgeKind(str, Enum):
    """Edge kind constants for the unified graph schema."""

    # Core semantic kinds
    INCLUDE = "include"
    IMPORT = "import"
    SOURCES = "sources"
    DEPENDS_ON = "depends_on"
    LINK_LIBRARIES = "link_libraries"
    CONTAINS = "contains"
    DEFINED_BY = "defined_by"
    INCLUDE_DIRS = "include_dirs"
    ALIAS_OF = "alias_of"
    IMPLEMENTS_NATIVE = "implements_native"
    CONSUMES = "consumes"
    PRODUCES = "produces"
    PART_OF = "part_of"
    AFFECTS = "affects"
    LINKS = "links"


class NodeSpec(BaseModel):
    """Structured representation of a graph node.

    This adds a thin validation layer on top of the low-level GraphBackend API
    while keeping enough flexibility to carry arbitrary attributes.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    id: Annotated[str, Field(..., description="Canonical node identifier")]
    type: Annotated[NodeType, Field(..., description="Node type enum")]

    label: Annotated[
        Optional[str],
        Field(
            default=None,
            description="Human-friendly label; defaults to id during export when omitted",
        ),
    ]
    src_path: Annotated[
        Optional[str],
        Field(
            default=None,
            description="Absolute source path (preferred for path-based nodes)",
        ),
    ]
    path: Annotated[
        Optional[str],
        Field(
            default=None,
            description="Legacy path attribute; mirrored into src_path when present",
        ),
    ]
    name: Annotated[
        Optional[str],
        Field(
            default=None,
            description="Short display name (typically file/target name)",
        ),
    ]
    parser_name: Annotated[
        Optional[str],
        Field(
            default=None,
            description="Name of parser / hook that produced this node",
        ),
    ]
    confidence: Annotated[
        float,
        Field(
            default=1.0,
            ge=0.0,
            le=1.0,
            description="Confidence score in [0.0, 1.0]",
        ),
    ]
    over_approx: Annotated[
        bool,
        Field(
            default=False,
            description="Whether this node represents an over-approximation",
        ),
    ]
    uncertainty_category: Annotated[
        str,
        Field(
            default="definite",
            description="Uncertainty level: definite / probable / conditional",
        ),
    ]
    uncertainty_reasons: Annotated[
        list[str],
        Field(
            default_factory=list,
            description="Machine-readable tags describing sources of uncertainty",
        ),
    ]
    uncertainty_explanation: Annotated[
        Optional[str],
        Field(
            default=None,
            description="Optional human-readable explanation for uncertainty",
        ),
    ]
    # Provisional nodes are created implicitly (e.g. as edge endpoints) and
    # should ideally be replaced or refined later.
    provisional: Annotated[
        bool,
        Field(
            default=False,
            description="Whether this node is a provisional placeholder",
        ),
    ]

    # Free-form attributes carried through to the backend.
    attrs: Annotated[Dict[str, Any], Field(default_factory=dict)]

    @field_validator("id")
    @classmethod
    def _check_id_non_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("Node id must be a non-empty string")
        return value

    def to_backend_attrs(self) -> Dict[str, Any]:
        """Convert this spec into a backend attribute mapping."""
        payload: Dict[str, Any] = {
            "type": self.type.value,
            "confidence": self.confidence,
            "over_approx": self.over_approx,
        }
        # Uncertainty annotations are exported for downstream tools but remain
        # orthogonal to any business logic (license/security).
        payload["uncertainty_category"] = self.uncertainty_category
        if self.uncertainty_reasons:
            payload["uncertainty_reasons"] = list(self.uncertainty_reasons)
        if self.uncertainty_explanation:
            payload["uncertainty_explanation"] = self.uncertainty_explanation

        if self.label:
            payload["label"] = self.label
        if self.src_path:
            payload["src_path"] = self.src_path
        if self.path:
            payload["path"] = self.path
        if self.name:
            payload["name"] = self.name
        if self.parser_name:
            payload["parser_name"] = self.parser_name
        if self.provisional:
            payload["provisional"] = True

        payload.update(self.attrs)
        return payload


class EdgeSpec(BaseModel):
    """Structured representation of a graph edge."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    source: Annotated[str, Field(..., description="Source node id")]
    target: Annotated[str, Field(..., description="Target node id")]
    kind: Annotated[EdgeKind, Field(..., description="Semantic edge kind")]

    parser_name: Annotated[
        Optional[str],
        Field(
            default=None,
            description="Name of parser / hook that produced this edge",
        ),
    ]
    confidence: Annotated[
        float,
        Field(
            default=1.0,
            ge=0.0,
            le=1.0,
            description="Confidence score in [0.0, 1.0]",
        ),
    ]
    over_approx: Annotated[
        bool,
        Field(
            default=False,
            description="Whether this edge represents an over-approximation",
        ),
    ]
    uncertainty_category: Annotated[
        str,
        Field(
            default="definite",
            description="Uncertainty level: definite / probable / conditional",
        ),
    ]
    uncertainty_reasons: Annotated[
        list[str],
        Field(
            default_factory=list,
            description="Machine-readable tags describing sources of uncertainty",
        ),
    ]
    uncertainty_explanation: Annotated[
        Optional[str],
        Field(
            default=None,
            description="Optional human-readable explanation for uncertainty",
        ),
    ]

    attrs: Annotated[Dict[str, Any], Field(default_factory=dict)]

    @field_validator("source", "target")
    @classmethod
    def _check_endpoint_non_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("Edge endpoints must be non-empty strings")
        return value

    def to_backend_attrs(self) -> Dict[str, Any]:
        """Convert this spec into a backend attribute mapping."""
        payload: Dict[str, Any] = {
            "kind": self.kind.value,
            "confidence": self.confidence,
            "over_approx": self.over_approx,
        }
        if self.parser_name:
            payload["parser_name"] = self.parser_name

        # Uncertainty annotations are exported for downstream tools but remain
        # orthogonal to any business logic (license/security).
        payload["uncertainty_category"] = self.uncertainty_category
        if self.uncertainty_reasons:
            payload["uncertainty_reasons"] = list(self.uncertainty_reasons)
        if self.uncertainty_explanation:
            payload["uncertainty_explanation"] = self.uncertainty_explanation

        payload.update(self.attrs)
        return payload


def validate_node(spec: NodeSpec, root_path: Optional[Path] = None) -> None:
    """Apply light-weight semantic validation to a node spec.

    This is intentionally conservative for now: it logs suspicious patterns
    instead of raising, so that legacy callers continue to function while
    violations are surfaced during development.
    """
    if spec.type is NodeType.UNKNOWN and not spec.provisional:
        raise ValueError(
            f"Node {spec.id} uses UNKNOWN type without provisional flag; "
            "please assign a concrete NodeType."
        )

    # Warn on plainly non-canonical ids (but do not reject yet)
    if not (
        spec.id.startswith("//")
        or spec.id.startswith("dep_graph:")
        or ":" in spec.id
    ):
        logger.debug("Node %s uses non-canonical id format: %s", spec.id, spec.id)

    # Enforce basic path semantics for path-like node types.
    if spec.provisional:
        return

    path_like_types = {
        NodeType.CONFIG,
        NodeType.MODULE,
        NodeType.SUBDIRECTORY,
    }

    if spec.type in path_like_types:
        if not spec.src_path:
            raise ValueError(
                f"Node {spec.id} ({spec.type.value}) must provide src_path "
                "for path-like node types."
            )
        if root_path is not None:
            try:
                _normalize_path_id(spec.src_path, root_path)
            except Exception as exc:  # noqa: BLE001
                raise ValueError(
                    f"Node {spec.id} ({spec.type.value}) has src_path={spec.src_path!r} "
                    f"which cannot be normalized relative to root_path={root_path}"
                ) from exc


def validate_edge(spec: EdgeSpec) -> None:
    """Apply light-weight semantic validation to an edge spec."""
    # At this stage we only log; richer invariants (e.g. kind↔NodeType) can be
    # added gradually without breaking existing callers.
    if spec.confidence < 0.0 or spec.confidence > 1.0:
        raise ValueError(
            f"Edge {spec.source} -> {spec.target} has out-of-range confidence "
            f"{spec.confidence:.3f}; expected value in [0.0, 1.0]."
        )


def edge_attrs(kind: EdgeKind | str, parser_name: str, **extras: Any) -> Dict[str, Any]:
    """Helper to build standardized edge attribute dicts.

    This is a compatibility wrapper for legacy code that still expects a
    plain mapping instead of EdgeSpec. New code should prefer EdgeSpec
    directly, but this helper keeps simple cases concise.
    """
    kind_value = kind.value if isinstance(kind, EdgeKind) else str(kind)
    payload: Dict[str, Any] = {
        "kind": kind_value,
        "label": kind_value,
        "parser_name": parser_name,
    }
    payload.update({k: v for k, v in extras.items() if v is not None})
    return payload


def node_attrs(node_type: NodeType | str, parser_name: str, **extras: Any) -> Dict[str, Any]:
    """Helper to build standardized node attribute dicts.

    This mirrors the legacy core.schema.node_attrs helper while using the
    unified NodeType enum as the source of truth.
    """
    ntype_value = node_type.value if isinstance(node_type, NodeType) else str(node_type)
    payload: Dict[str, Any] = {"type": ntype_value, "parser_name": parser_name}
    payload.update({k: v for k, v in extras.items() if v is not None})
    return payload
