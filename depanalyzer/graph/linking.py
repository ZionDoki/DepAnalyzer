"""Linking-related graph helpers and enums.

This module centralizes small pieces of metadata that describe how
edges in the unified graph were derived. It is intentionally minimal
so that higher-level linkers in parsers can depend on it without
pulling in runtime-specific code.
"""

from __future__ import annotations

import logging
from enum import Enum

logger = logging.getLogger("depanalyzer.graph.linking")


class LinkClass(str, Enum):
    """Classification for edges based on their derivation.

    This does *not* change graph semantics; it is purely a hint for
    analyses (license propagation, deadcode, etc.) about where an edge
    comes from.

    Attributes:
        SEMANTIC: Intrinsic code relationships such as include/import.
        BUILD_CONFIG: Relationships derived from build configuration or
            ecosystem-specific linkers (module membership, shared libs).
        CROSS_GRAPH: Relationships that connect separate graphs or
            dependency transactions (for example proxy edges).
    """

    SEMANTIC = "semantic"
    BUILD_CONFIG = "build_config"
    CROSS_GRAPH = "cross_graph"
