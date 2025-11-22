"""Public policy and hook interfaces for runtime lifecycle customization.

This module defines protocol-based extension points that allow library
consumers to customize how a transaction behaves at key lifecycle
boundaries without modifying the core orchestrator.

The interfaces are deliberately conservative and operate on typed
context objects instead of the raw ``Transaction`` to keep the public
API surface small and stable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Protocol, runtime_checkable

from depanalyzer.graph import GraphManager
from depanalyzer.graph.projection_config import ProjectionConfig
from depanalyzer.runtime.context import (
    AnalyzeContext,
    ExportContext,
    JoinContext,
    TransactionContext,
)
from depanalyzer.runtime.lifecycle import LifecyclePhase


@runtime_checkable
class LifecycleHook(Protocol):
    """Hook that executes before and after a specific lifecycle phase.

    Implementations may inspect or mutate the provided context, but
    must not throw exceptions that break the transaction. The runtime
    will catch and log hook failures.

    Attributes:
        phase: LifecyclePhase this hook is associated with.
    """

    phase: LifecyclePhase

    def before(self, ctx: TransactionContext) -> None:
        """Execute logic before the associated phase starts.

        Args:
            ctx: Transaction context snapshot at phase entry.
        """

    def after(self, ctx: TransactionContext) -> None:
        """Execute logic after the associated phase completes.

        Args:
            ctx: Transaction context snapshot at phase exit.
        """


@dataclass
class CodeDependencyContext:
    """Context for code dependency mapping.

    This is constructed for each parsed source file and passed to a
    ``CodeDependencyMapper`` which is responsible for updating the
    graph based on the parse result.

    Args:
        transaction_ctx: Transaction-scoped context.
        file_path: Path to the parsed code file.
        parse_result: Parse result dictionary emitted by a BaseCodeParser.
    """

    transaction_ctx: TransactionContext
    file_path: Path
    parse_result: Dict[str, Any]


@runtime_checkable
class CodeDependencyMapper(Protocol):
    """Map code parser results into graph nodes and edges."""

    def map(self, ctx: CodeDependencyContext) -> None:
        """Apply dependency mapping for a single source file.

        Args:
            ctx: Code dependency context with parse result and graph.
        """


@dataclass
class ProjectionContext:
    """Context for asset→artifact projection policies.

    Args:
        transaction_ctx: Transaction-scoped context.
        graph: GraphManager instance to operate on.
        projection_config: ProjectionConfig controlling projection behavior.
    """

    transaction_ctx: TransactionContext
    graph: GraphManager
    projection_config: ProjectionConfig


@runtime_checkable
class AssetProjectionPolicy(Protocol):
    """Policy for deriving asset→artifact projection edges."""

    def project(self, ctx: ProjectionContext) -> None:
        """Execute projection on the given graph.

        Args:
            ctx: ProjectionContext with graph and configuration.
        """


@runtime_checkable
class JoinPolicy(Protocol):
    """Policy executed during the JOIN phase."""

    def join(self, ctx: JoinContext) -> None:
        """Execute cross-ecosystem and cross-language joining.

        Args:
            ctx: JoinContext scoped to the JOIN phase.
        """


@runtime_checkable
class AnalyzePolicy(Protocol):
    """Policy executed during the ANALYZE phase."""

    def analyze(self, ctx: AnalyzeContext) -> None:
        """Run additional analysis passes on the transaction graph.

        Args:
            ctx: AnalyzeContext scoped to the ANALYZE phase.
        """


@runtime_checkable
class ExportPolicy(Protocol):
    """Optional policy executed during the EXPORT phase.

    This is defined for future extension and is not currently wired
    into the Transaction lifecycle.
    """

    def export(self, ctx: ExportContext) -> None:
        """Run custom export logic.

        Args:
            ctx: ExportContext scoped to the EXPORT phase.
        """


AssetProjectionStrategy = AssetProjectionPolicy
JoinStrategy = JoinPolicy
AnalyzeStrategy = AnalyzePolicy
ExportStrategy = ExportPolicy

__all__ = [
    "LifecycleHook",
    "CodeDependencyContext",
    "CodeDependencyMapper",
    "ProjectionContext",
    "AssetProjectionPolicy",
    "JoinPolicy",
    "AnalyzePolicy",
    "ExportPolicy",
    "AssetProjectionStrategy",
    "JoinStrategy",
    "AnalyzeStrategy",
    "ExportStrategy",
]
