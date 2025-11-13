"""Lifecycle phase definitions for transaction execution.

This module defines the canonical execution phases that every transaction
follows: Acquire→Detect→Parse→ResolveDeps→Join→Analyze→Export.
"""

from enum import Enum, auto


class LifecyclePhase(Enum):
    """Execution phases for a transaction.

    Each phase represents a distinct stage in the analysis workflow:
    - ACQUIRE: Source code acquisition (local or Git)
    - DETECT: Parallel detection of targets (Hvigor/CMake/Prebuilt/Deps)
    - PARSE: Parallel parsing of detected targets
    - RESOLVE_DEPS: Unified dependency resolution
    - JOIN: Hook execution for cross-domain associations
    - ANALYZE: Analysis on the transaction graph
    - EXPORT: Export results in various formats
    """

    ACQUIRE = auto()
    DETECT = auto()
    PARSE = auto()
    RESOLVE_DEPS = auto()
    JOIN = auto()
    ANALYZE = auto()
    EXPORT = auto()

    def __str__(self) -> str:
        """Return human-readable phase name.

        Returns:
            str: Phase name in title case.
        """
        return self.name.replace("_", " ").title()
