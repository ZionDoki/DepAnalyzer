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
from typing import Annotated, Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from depanalyzer.utils.path_utils import normalize_node_id as _normalize_path_id

logger = logging.getLogger("depanalyzer.graph.models.schema")


# =============================================================================
# Domain-Specific Node Type Enums
# =============================================================================
# These enums categorize node types by their domain/ecosystem.
# The unified NodeType enum below aggregates all types for backward compatibility.


class CoreNodeType(str, Enum):
    """Core node types shared across all ecosystems.

    These are fundamental building blocks that appear in any dependency graph
    regardless of the specific build system or language.
    """

    CODE = "code"
    CONFIG = "config"
    MODULE = "module"
    ARTIFACT = "artifact"
    EXTERNAL_DEP = "external_dep"
    EXTERNAL_LIBRARY = "external_library"
    LICENSE = "license"
    PROXY = "proxy"
    UNKNOWN = "unknown"


class CppNodeType(str, Enum):
    """C/C++ and CMake specific node types.

    These types are used when parsing CMake projects, Makefiles,
    or other C/C++ build systems.
    """

    SYSTEM_HEADER = "system_header"
    PROJECT_HEADER = "project_header"
    HEADER = "header"  # Legacy, prefer SYSTEM_HEADER or PROJECT_HEADER
    TARGET = "target"
    SUBDIRECTORY = "subdirectory"
    TOOLCHAIN = "toolchain"
    BUILD_CONFIG = "build_config"
    SHARED_LIBRARY = "shared_library"
    STATIC_LIBRARY = "static_library"
    EXECUTABLE = "executable"


class HvigorNodeType(str, Enum):
    """HVigor (OpenHarmony/HarmonyOS) specific node types.

    These types are used when parsing HVigor build configurations
    for OpenHarmony applications.
    """

    HAP = "hap"  # Harmony Ability Package
    HAR = "har"  # Harmony Archive (library)
    HSP = "hsp"  # Harmony Shared Package


class AnalysisNodeType(str, Enum):
    """Analysis-derived node types.

    These types are created during graph analysis phases,
    not during initial parsing.
    """

    SCC_CLUSTER = "scc_cluster"
    CODE_SCC_CLUSTER = "code_scc_cluster"


class MiscNodeType(str, Enum):
    """Miscellaneous node types.

    Types that don't fit neatly into other categories.
    """

    PROCESS = "process"
    ASSET = "asset"


# =============================================================================
# Unified NodeType Enum (Backward Compatible)
# =============================================================================


class NodeType(str, Enum):
    """Node type constants for the unified graph schema.

    This enum aggregates all domain-specific node types into a single
    namespace for backward compatibility. New code should consider using
    the domain-specific enums (CoreNodeType, CppNodeType, etc.) for
    better type safety and documentation.

    Node types are organized into categories:
    - Core: Fundamental types shared across ecosystems
    - C++/CMake: Types specific to C/C++ build systems
    - HVigor: Types specific to OpenHarmony/HarmonyOS
    - Analysis: Types created during graph analysis
    - Misc: Other types
    """

    # --- Core types (from CoreNodeType) ---
    CODE = "code"
    CONFIG = "config"
    MODULE = "module"
    ARTIFACT = "artifact"
    EXTERNAL_DEP = "external_dep"
    EXTERNAL_LIBRARY = "external_library"
    LICENSE = "license"
    PROXY = "proxy"
    UNKNOWN = "unknown"

    # --- C++/CMake types (from CppNodeType) ---
    SYSTEM_HEADER = "system_header"
    PROJECT_HEADER = "project_header"
    HEADER = "header"
    TARGET = "target"
    SUBDIRECTORY = "subdirectory"
    TOOLCHAIN = "toolchain"
    BUILD_CONFIG = "build_config"
    SHARED_LIBRARY = "shared_library"
    STATIC_LIBRARY = "static_library"
    EXECUTABLE = "executable"

    # --- HVigor types (from HvigorNodeType) ---
    HAP = "hap"
    HAR = "har"
    HSP = "hsp"

    # --- Analysis types (from AnalysisNodeType) ---
    SCC_CLUSTER = "scc_cluster"
    CODE_SCC_CLUSTER = "code_scc_cluster"

    # --- Misc types (from MiscNodeType) ---
    PROCESS = "process"
    ASSET = "asset"

    @classmethod
    def is_core_type(cls, node_type: "NodeType") -> bool:
        """Check if a node type is a core type."""
        core_values = {t.value for t in CoreNodeType}
        return node_type.value in core_values

    @classmethod
    def is_cpp_type(cls, node_type: "NodeType") -> bool:
        """Check if a node type is a C++/CMake type."""
        cpp_values = {t.value for t in CppNodeType}
        return node_type.value in cpp_values

    @classmethod
    def is_hvigor_type(cls, node_type: "NodeType") -> bool:
        """Check if a node type is a HVigor type."""
        hvigor_values = {t.value for t in HvigorNodeType}
        return node_type.value in hvigor_values

    @classmethod
    def is_analysis_type(cls, node_type: "NodeType") -> bool:
        """Check if a node type is an analysis-derived type."""
        analysis_values = {t.value for t in AnalysisNodeType}
        return node_type.value in analysis_values

    @classmethod
    def get_category(cls, node_type: "NodeType") -> str:
        """Get the category name for a node type.

        Returns:
            One of: 'core', 'cpp', 'hvigor', 'analysis', 'misc'
        """
        if cls.is_core_type(node_type):
            return "core"
        if cls.is_cpp_type(node_type):
            return "cpp"
        if cls.is_hvigor_type(node_type):
            return "hvigor"
        if cls.is_analysis_type(node_type):
            return "analysis"
        return "misc"


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
            description="Absolute source path for path-based nodes",
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
    external_graph_id: Annotated[
        Optional[str],
        Field(
            default=None,
            description="ID of an external graph that this node references (Graph-of-Graphs)",
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
        if self.name:
            payload["name"] = self.name
        if self.parser_name:
            payload["parser_name"] = self.parser_name
        if self.external_graph_id:
            payload["external_graph_id"] = self.external_graph_id
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


