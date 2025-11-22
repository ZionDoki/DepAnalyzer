"""
Transaction state container with centralized state management.

This module provides a centralized state container for Transaction execution,
replacing the scattered state attributes previously stored in TransactionBase.
All state mutations are tracked through the update_phase_result() method
for better debugging and observability.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from depanalyzer.runtime.graph_config import GraphBuildConfig
from depanalyzer.runtime.lifecycle import LifecyclePhase
from depanalyzer.runtime.policies import (
    AnalyzePolicy,
    AssetProjectionPolicy,
    CodeDependencyMapper,
    JoinPolicy,
    LifecycleHook,
)

if TYPE_CHECKING:  # pragma: no cover
    from depanalyzer.runtime.dependency_collector import DependencyCollector
    from depanalyzer.runtime.eventbus import EventBus
    from depanalyzer.runtime.progress import ProgressManager
    from depanalyzer.runtime.protocols import TransactionFactory
    from depanalyzer.runtime.worker import Worker
    from depanalyzer.runtime.workspace import Workspace
    from depanalyzer.graph import GraphManager

logger = logging.getLogger("depanalyzer.transaction.state")


@dataclass
class TransactionState:
    """
    Centralized state container for Transaction execution.

    This dataclass encapsulates all state needed throughout the transaction
    lifecycle, including configuration, runtime components, and phase results.

    Benefits:
    - Clear state lifecycle (initialization → execution → cleanup)
    - Centralized state updates with logging
    - Easy to serialize/deserialize
    - Testable in isolation

    Attributes:
        # Basic Configuration
        transaction_id: Unique identifier for this transaction
        source: Source to analyze (path, URL, etc.)
        graph_id: Optional graph identifier
        max_workers: Max parallel workers for task execution
        max_dependency_depth: Max recursion depth for dependency resolution
        parent_transaction_id: Parent transaction ID (for nested transactions)
        enable_dependency_resolution: Enable third-party dependency resolution
        max_dependencies: Max number of dependencies to resolve

        # Cache Paths
        graph_cache_root: Root directory for graph cache
        dep_cache_root: Root directory for dependency cache
        workspace_cache_root: Root directory for workspace cache

        # Configuration Objects
        graph_build_config: Configuration for graph building

        # Policies and Hooks
        lifecycle_hooks: Lifecycle hooks for before/after phase callbacks
        code_dependency_mappers: Ecosystem-specific code dependency mappers
        default_code_dependency_mapper: Default fallback mapper
        asset_projection_policy: Policy for asset projection
        join_policies: Policies for JOIN phase
        analyze_policies: Policies for ANALYZE phase

        # Factory Injection (for child transactions)
        child_transaction_factory: Factory for creating child transactions
                                   (injected to avoid circular imports)

        # Runtime Components (lazy-initialized)
        workspace: Source code workspace
        worker: Task queue worker pool
        eventbus: Event bus for parser communication
        graph_manager: Graph building manager
        dependency_collector: Dependency collector
        progress_manager: Progress tracking manager

        # Phase Results (shared between phases)
        detected_targets: Detected build targets (from DETECT phase)
        current_phase: Currently executing phase
        start_time: Transaction start timestamp
    """

    # ===== Basic Configuration =====
    transaction_id: str
    source: str
    graph_id: Optional[str] = None
    max_workers: int = 8
    max_dependency_depth: int = 3
    parent_transaction_id: Optional[str] = None
    enable_dependency_resolution: bool = False
    max_dependencies: Optional[int] = None

    # ===== Cache Paths =====
    graph_cache_root: Optional[Path] = None
    dep_cache_root: Optional[Path] = None
    workspace_cache_root: Optional[Path] = None

    # ===== Configuration Objects =====
    graph_build_config: GraphBuildConfig = field(
        default_factory=GraphBuildConfig.default
    )

    # ===== Policies and Hooks =====
    lifecycle_hooks: List[LifecycleHook] = field(default_factory=list)
    code_dependency_mappers: Dict[str, CodeDependencyMapper] = field(default_factory=dict)
    default_code_dependency_mapper: Optional[CodeDependencyMapper] = None
    asset_projection_policy: Optional[AssetProjectionPolicy] = None
    join_policies: List[JoinPolicy] = field(default_factory=list)
    analyze_policies: List[AnalyzePolicy] = field(default_factory=list)

    # ===== Factory Injection =====
    child_transaction_factory: Optional["TransactionFactory"] = None

    # ===== Runtime Components (lazy-initialized) =====
    workspace: Optional["Workspace"] = None
    worker: Optional["Worker"] = None
    eventbus: Optional["EventBus"] = None
    graph_manager: Optional["GraphManager"] = None
    dependency_collector: Optional["DependencyCollector"] = None
    progress_manager: Optional["ProgressManager"] = None

    # ===== Phase Results =====
    detected_targets: Dict[str, List[Path]] = field(default_factory=dict)
    current_phase: Optional[LifecyclePhase] = None
    start_time: float = 0.0
    graph_cache_path: Optional[Path] = None  # Set by ExportPhase

    @property
    def asset_projection_strategy(self) -> Optional[AssetProjectionPolicy]:
        """Compatibility alias for the configured asset projection policy."""
        return self.asset_projection_policy

    @asset_projection_strategy.setter
    def asset_projection_strategy(self, policy: Optional[AssetProjectionPolicy]) -> None:
        self.asset_projection_policy = policy

    @property
    def join_strategies(self) -> List[JoinPolicy]:
        """Compatibility alias for configured join policies."""
        return self.join_policies

    @join_strategies.setter
    def join_strategies(self, policies: List[JoinPolicy]) -> None:
        self.join_policies = policies

    @property
    def analyze_strategies(self) -> List[AnalyzePolicy]:
        """Compatibility alias for configured analyze policies."""
        return self.analyze_policies

    @analyze_strategies.setter
    def analyze_strategies(self, policies: List[AnalyzePolicy]) -> None:
        self.analyze_policies = policies

    def update_phase_result(
        self,
        phase: LifecyclePhase,
        key: str,
        value: Any,
    ) -> None:
        """
        Centralized state update with logging and validation.

        This method provides a single point for all state mutations,
        making it easier to debug "who changed this variable" issues
        and track state changes over time.

        Args:
            phase: The phase making the update
            key: State attribute name to update
            value: New value for the attribute

        Example:
            state.update_phase_result(
                LifecyclePhase.DETECT,
                'detected_targets',
                {'python': [Path('setup.py')]}
            )

        Notes:
            - Logs all state updates for debugging
            - Warns if creating new attributes (potential typo)
            - Can be extended with validation logic or hooks
        """
        # Log the update for debugging
        value_repr = (
            f"{type(value).__name__}(len={len(value)})"
            if hasattr(value, "__len__")
            else f"{type(value).__name__}"
        )
        logger.debug(
            "[%s] Phase %s updated state[%s] = %s",
            self.transaction_id[:8],
            phase.name,
            key,
            value_repr,
        )

        # Warn if creating a new attribute (potential typo)
        if not hasattr(self, key):
            logger.warning(
                "Phase %s creating new state attribute: %s",
                phase.name,
                key,
            )

        # Update the state
        setattr(self, key, value)

    def get_phase_result(self, key: str, default: Any = None) -> Any:
        """
        Safely read state with default fallback.

        Args:
            key: State attribute name
            default: Default value if attribute doesn't exist

        Returns:
            The attribute value or default

        Example:
            dependencies = state.get_phase_result('resolved_dependencies', [])
        """
        return getattr(self, key, default)

    def __repr__(self) -> str:
        """Readable representation for debugging."""
        return (
            f"TransactionState("
            f"id={self.transaction_id[:8]}..., "
            f"source={self.source}, "
            f"phase={self.current_phase.name if self.current_phase else None})"
        )
