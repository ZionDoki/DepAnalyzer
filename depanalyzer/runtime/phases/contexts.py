"""
Phase-specific context definitions.

This module extends the base contexts from depanalyzer.runtime.context
with additional fields needed for the new phase architecture. It also
provides type aliases and factory functions for creating contexts.

The contexts are designed to be:
1. Immutable (frozen dataclasses) - prevent accidental mutations
2. Explicit dependencies - each phase declares its inputs clearly
3. Testable - easy to construct for testing individual phases
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

# Re-export base contexts for convenience
from depanalyzer.runtime.context import (
    TransactionContext,
    DetectContext,
    ParseContext,
    ResolveDepsContext,
    JoinContext,
    AnalyzeContext,
    ExportContext,
)

if TYPE_CHECKING:  # pragma: no cover
    from depanalyzer.runtime.protocols import TransactionFactory
    from depanalyzer.runtime.workspace import Workspace


@dataclass(frozen=True)
class AcquireContext(TransactionContext):
    """
    Context for the ACQUIRE phase.

    This phase acquires source code from local paths or Git repositories
    and prepares the workspace.

    Attributes:
        source_cache_root: Optional cache root for workspace downloads
    """
    # Note: TransactionContext already has 'source' and 'workspace' fields
    # We only need to add phase-specific fields here


# For the new architecture, we extend existing contexts with factory protocol
@dataclass(frozen=True)
class EnhancedResolveDepsContext(ResolveDepsContext):
    """
    Enhanced RESOLVE_DEPS context with factory injection.

    Extends the base ResolveDepsContext with a TransactionFactory
    to create child transactions without circular imports.

    Attributes:
        child_transaction_factory: Factory for creating child transactions
    """
    child_transaction_factory: Optional["TransactionFactory"] = None


__all__ = [
    # Re-exported base contexts
    "TransactionContext",
    "DetectContext",
    "ParseContext",
    "ResolveDepsContext",
    "JoinContext",
    "AnalyzeContext",
    "ExportContext",
    # New contexts
    "AcquireContext",
    "EnhancedResolveDepsContext",
]
