"""Lifecycle context dataclasses for transaction execution.

These lightweight data containers expose a structured view of the
transaction state to lifecycle hooks and strategy implementations.

The contexts are intentionally read-only snapshots assembled by the
``Transaction`` at phase boundaries. They do not own resources and
should not contain business logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from depanalyzer.graph import GraphManager
from depanalyzer.graph.contract_registry import ContractRegistry
from depanalyzer.runtime.eventbus import EventBus
from depanalyzer.runtime.graph_config import GraphBuildConfig
from depanalyzer.runtime.lifecycle import LifecyclePhase
from depanalyzer.runtime.workspace import Workspace
from depanalyzer.runtime.progress import ProgressManager


@dataclass
class TransactionContext:
    """Base context shared by all lifecycle phases.

    This context captures transaction-wide state that is stable across
    phases. Phase-specific contexts subclass this dataclass and extend
    it with additional fields.

    Args:
        transaction_id: Unique identifier for the transaction.
        graph_id: Optional graph identifier associated with this run.
        source: Input source (local path or Git URL).
        workspace: Workspace representing the acquired source tree.
        graph: Optional GraphManager instance for the current transaction.
        graph_build_config: Aggregate graph build configuration.
        eventbus: Event bus for parser and hook communication.
        contract_registry: Global contract registry singleton.
        progress_manager: Optional progress manager for live updates.
        enable_dependency_resolution: Whether third-party deps are resolved.
        max_dependency_depth: Maximum depth for dependency resolution.
        max_dependencies: Optional global cap for resolved dependencies.
        current_phase: Current lifecycle phase when the context is created.
    """

    transaction_id: str
    graph_id: Optional[str]
    source: str
    workspace: Workspace
    graph: Optional[GraphManager]
    graph_build_config: GraphBuildConfig
    eventbus: EventBus
    contract_registry: ContractRegistry
    progress_manager: Optional[ProgressManager]
    enable_dependency_resolution: bool
    max_dependency_depth: int
    max_dependencies: Optional[int]
    current_phase: Optional[LifecyclePhase]


@dataclass
class DetectContext(TransactionContext):
    """Context for the DETECT phase.

    Args:
        detected_targets: Mapping from ecosystem name to detected target paths.
    """

    detected_targets: Dict[str, list[Path]]


@dataclass
class ParseContext(TransactionContext):
    """Context for the PARSE phase.

    Args:
        detected_targets: Mapping from ecosystem name to detected targets.
        code_files_to_parse: Mapping from ecosystem name to code file paths.
    """

    detected_targets: Dict[str, list[Path]]
    code_files_to_parse: Dict[str, list[Path]]


@dataclass
class ResolveDepsContext(TransactionContext):
    """Context for the RESOLVE_DEPS phase.

    Args:
        resolved_deps: List of resolved dependency descriptors.
        dependency_specs: List of dependency specifications collected so far.
    """

    resolved_deps: list[Any]
    dependency_specs: list[Any]


@dataclass
class JoinContext(TransactionContext):
    """Context for the JOIN phase.

    Additional JOIN-specific metadata can be added here in the future.
    """


@dataclass
class AnalyzeContext(TransactionContext):
    """Context for the ANALYZE phase.

    Additional ANALYZE-specific metadata can be added here in the future.
    """


@dataclass
class ExportContext(TransactionContext):
    """Context for the EXPORT phase.

    Args:
        output_path: Optional output path used for export.
    """

    output_path: Optional[Path]


__all__ = [
    "TransactionContext",
    "DetectContext",
    "ParseContext",
    "ResolveDepsContext",
    "JoinContext",
    "AnalyzeContext",
    "ExportContext",
]
