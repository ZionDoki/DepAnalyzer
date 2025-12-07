"""
Transaction orchestration using the new phase-based architecture.

This module provides the Transaction facade class that maintains backward
compatibility while internally delegating to the PhaseOrchestrator.
"""

import uuid
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Mapping, Optional, Sequence

from depanalyzer.parsers.cpp.code_dependency_mapper import CppCodeDependencyMapper
from depanalyzer.parsers.hvigor.code_dependency_mapper import HvigorCodeDependencyMapper
from depanalyzer.parsers.maven.code_dependency_mapper import MavenCodeDependencyMapper
from depanalyzer.parsers.npm.code_dependency_mapper import NpmCodeDependencyMapper
from depanalyzer.runtime.task_types import TransactionResult
from depanalyzer.runtime.graph_config import GraphBuildConfig
from depanalyzer.runtime.orchestrator import PhaseOrchestrator
from depanalyzer.runtime.policies import (
    AnalyzePolicy,
    AssetProjectionPolicy,
    CodeDependencyMapper,
    DefaultAssetProjectionPolicy,
    DefaultCodeDependencyMapper,
    FileCompletenessJoinPolicy,
    JoinPolicy,
    LicenseAttachmentPolicy,
    LifecycleHook,
)
from depanalyzer.runtime.transaction_state import TransactionState
from depanalyzer.runtime.workspace import Workspace

if TYPE_CHECKING:  # pragma: no cover
    from depanalyzer.runtime.progress import ProgressManager


class Transaction:
    """
    Transaction facade for dependency analysis.

    This class provides a backward-compatible API while internally using
    the new phase-based architecture (PhaseOrchestrator + 7 phase classes).

    The Transaction class also implements the TransactionFactory protocol,
    allowing it to create child transactions for dependency resolution
    without circular imports.

    Example:
        tx = Transaction(
            source="/path/to/project",
            graph_id="my_project",
            max_workers=8,
        )
        result = tx.run()
        print(f"Graph has {result.node_count} nodes")
    """

    def __init__(
        self,
        source: str,
        graph_id: Optional[str] = None,
        max_workers: int = 8,
        max_dependency_depth: int = 3,
        parent_transaction_id: Optional[str] = None,
        transaction_id: Optional[str] = None,
        progress_manager: Optional["ProgressManager"] = None,
        enable_dependency_resolution: bool = False,
        max_dependencies: Optional[int] = None,
        graph_cache_root: Optional[Path] = None,
        dep_cache_root: Optional[Path] = None,
        workspace_cache_root: Optional[Path] = None,
        graph_metadata: Optional[Dict[str, Any]] = None,
        graph_build_config: Optional[GraphBuildConfig] = None,
        lifecycle_hooks: Optional[Sequence[LifecycleHook]] = None,
        code_dependency_mappers: Optional[Mapping[str, CodeDependencyMapper]] = None,
        asset_projection_policy: Optional[AssetProjectionPolicy] = None,
        join_policies: Optional[Sequence[JoinPolicy]] = None,
        analyze_policies: Optional[Sequence[AnalyzePolicy]] = None,
    ) -> None:
        """
        Initialize transaction (maintains backward-compatible API).

        Args:
            source: Source to analyze (local path or Git URL)
            graph_id: Optional graph identifier
            max_workers: Max parallel workers for task execution
            max_dependency_depth: Max recursion depth for dependency resolution
            parent_transaction_id: Parent transaction ID (for nested transactions)
            transaction_id: Unique transaction ID (generated if not provided)
            progress_manager: Optional progress tracking manager
            enable_dependency_resolution: Enable third-party dependency resolution
            max_dependencies: Max number of dependencies to resolve
            graph_cache_root: Root directory for graph cache
            dep_cache_root: Root directory for dependency cache
            workspace_cache_root: Root directory for workspace cache
            graph_metadata: Optional initial graph metadata to seed GraphManager
            graph_build_config: Configuration for graph building
            lifecycle_hooks: Lifecycle hooks for before/after phase callbacks
            code_dependency_mappers: Ecosystem-specific code dependency mappers
            asset_projection_policy: Policy for asset projection
            join_policies: Policies for JOIN phase
            analyze_policies: Policies for ANALYZE phase
        """
        # Prepare graph build config and code dependency mappers
        _graph_build_config = graph_build_config or GraphBuildConfig.default()
        _explicit_join_supplied = join_policies is not None

        _code_dependency_mappers: Dict[str, CodeDependencyMapper] = {}
        if code_dependency_mappers is not None:
            _code_dependency_mappers.update(code_dependency_mappers)
        else:
            _code_dependency_mappers.setdefault("cpp", CppCodeDependencyMapper())
            _code_dependency_mappers.setdefault("hvigor", HvigorCodeDependencyMapper())
            _code_dependency_mappers.setdefault("maven", MavenCodeDependencyMapper())
            _code_dependency_mappers.setdefault("npm", NpmCodeDependencyMapper())

        # Join policies (append fallback when enabled)
        _join_policies = list(join_policies or [])
        try:
            if _graph_build_config.license_link.enabled:
                already_present = any(
                    isinstance(p, LicenseAttachmentPolicy) for p in _join_policies
                )
                if not already_present:
                    _join_policies.append(
                        LicenseAttachmentPolicy(_graph_build_config.license_link)
                    )
        except AttributeError:
            pass

        try:
            if _graph_build_config.fallback.enabled:
                _join_policies.append(FileCompletenessJoinPolicy(_graph_build_config.fallback))
        except AttributeError:
            pass

        asset_policy = asset_projection_policy or DefaultAssetProjectionPolicy()

        # Create TransactionState
        self._state = TransactionState(
            transaction_id=transaction_id or str(uuid.uuid4()),
            source=source,
            graph_id=graph_id,
            max_workers=max_workers,
            max_dependency_depth=max_dependency_depth,
            parent_transaction_id=parent_transaction_id,
            enable_dependency_resolution=enable_dependency_resolution,
            max_dependencies=max_dependencies,
            graph_cache_root=Path(graph_cache_root) if graph_cache_root else None,
            dep_cache_root=Path(dep_cache_root) if dep_cache_root else None,
            workspace_cache_root=(
                Path(workspace_cache_root) if workspace_cache_root else None
            ),
            graph_metadata=dict(graph_metadata or {}),
            graph_build_config=_graph_build_config,
            lifecycle_hooks=list(lifecycle_hooks or []),
            code_dependency_mappers=_code_dependency_mappers,
            default_code_dependency_mapper=DefaultCodeDependencyMapper(),
            asset_projection_policy=asset_policy,
            join_policies=_join_policies,
            analyze_policies=list(analyze_policies or []),
            # Initialize workspace
            workspace=Workspace(source, cache_root=workspace_cache_root),
            # Progress manager
            progress_manager=progress_manager,
        )

        # Inject self as factory (solves circular import for child transactions)
        self._state.child_transaction_factory = self.__class__

        # Create orchestrator
        self._orchestrator = PhaseOrchestrator(self._state)

    def run(self, output_path: Optional[Path] = None) -> TransactionResult:
        """
        Execute the full transaction lifecycle.

        This method delegates to PhaseOrchestrator which executes all 7 phases
        in order: ACQUIRE → DETECT → PARSE → RESOLVE_DEPS → JOIN → ANALYZE → EXPORT

        Args:
            output_path: Optional output path for exporting the graph.

        Returns:
            TransactionResult with execution details
        """
        normalized_output = Path(output_path) if output_path is not None else None
        self._orchestrator.output_path = normalized_output
        return self._orchestrator.run_all_phases()

    # ===== TransactionFactory Protocol Implementation =====

    @classmethod
    def create(
        cls, source: str, graph_id: Optional[str] = None, **kwargs
    ) -> "Transaction":
        """
        Factory method for creating Transaction instances.

        This method implements the TransactionFactory protocol, allowing
        ResolveDepsPhase to create child transactions without circular imports.

        Args:
            source: Source to analyze
            graph_id: Optional graph identifier
            **kwargs: Additional configuration parameters

        Returns:
            New Transaction instance
        """
        return cls(source=source, graph_id=graph_id, **kwargs)

    # ===== Backward Compatibility Properties =====

    @property
    def transaction_id(self) -> str:
        """Get transaction ID."""
        return self._state.transaction_id

    @property
    def graph_id(self) -> Optional[str]:
        """Get graph ID."""
        return self._state.graph_id

    @graph_id.setter
    def graph_id(self, value: Optional[str]) -> None:
        """Set graph ID (needed by AcquirePhase)."""
        self._state.graph_id = value

    @property
    def source(self) -> str:
        """Get source."""
        return self._state.source

    @property
    def max_workers(self) -> int:
        """Get max workers."""
        return self._state.max_workers

    @property
    def max_dependency_depth(self) -> int:
        """Get max dependency depth."""
        return self._state.max_dependency_depth

    @property
    def parent_transaction_id(self) -> Optional[str]:
        """Get parent transaction ID."""
        return self._state.parent_transaction_id

    @property
    def enable_dependency_resolution(self) -> bool:
        """Get dependency resolution flag."""
        return self._state.enable_dependency_resolution

    @property
    def max_dependencies(self) -> Optional[int]:
        """Get max dependencies."""
        return self._state.max_dependencies

    @property
    def workspace(self) -> Workspace:
        """Get workspace."""
        return self._state.workspace

    # ===== Serialization Support =====

    def __getstate__(self) -> dict:
        """
        Return pickle-safe state.

        Returns:
            Dictionary of serializable state
        """
        state = {
            "state": self._state,
            "orchestrator": None,  # Orchestrator is not serializable
        }

        # Clear non-serializable components from state
        if self._state:
            self._state.worker = None
            self._state.eventbus = None
            self._state.graph_manager = None
            self._state.dependency_collector = None
            self._state.progress_manager = None

        return state

    def __setstate__(self, state_dict: dict) -> None:
        """
        Restore transaction from pickled state.

        Args:
            state_dict: Pickled state dictionary
        """
        self._state = state_dict["state"]
        # Recreate orchestrator
        self._orchestrator = PhaseOrchestrator(self._state)

        # Re-initialize worker and eventbus (will be done by orchestrator)
        # Log deserialization
        logger = logging.getLogger("depanalyzer.transaction")
        logger.info(
            "Transaction %s deserialized in subprocess (PID: %d)",
            self._state.transaction_id,
            __import__("os").getpid(),
        )


__all__ = ["Transaction"]
