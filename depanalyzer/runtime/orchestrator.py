"""
Phase orchestrator for coordinating transaction lifecycle.

This module provides the PhaseOrchestrator class that manages the
execution of all 7 lifecycle phases in the correct order.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Dict, Optional

from depanalyzer.graph import GraphManager, GraphRegistry
from depanalyzer.graph.contract_registry import ContractRegistry
from depanalyzer.parsers.registry import EcosystemRegistry
from depanalyzer.runtime.context import ExportContext, TransactionContext
from depanalyzer.runtime.task_types import TransactionResult
from depanalyzer.runtime.dependency_collector import DependencyCollector
from depanalyzer.runtime.eventbus import EventBus
from depanalyzer.runtime.lifecycle import LifecyclePhase
from depanalyzer.runtime.transaction_state import TransactionState
from depanalyzer.runtime.worker import Worker

# Import all phase implementations
from depanalyzer.runtime.phases.acquire import AcquirePhase
from depanalyzer.runtime.phases.detect import DetectPhase
from depanalyzer.runtime.phases.parse import ParsePhase
from depanalyzer.runtime.phases.resolve_deps import ResolveDepsPhase
from depanalyzer.runtime.phases.join import JoinPhase
from depanalyzer.runtime.phases.analyze import AnalyzePhase
from depanalyzer.runtime.phases.export import ExportPhase
from depanalyzer.runtime.phases.base import BasePhase

logger = logging.getLogger("depanalyzer.transaction.orchestrator")

# Safe exceptions that should be caught
_SAFE_EXCEPTIONS = (
    RuntimeError,
    ValueError,
    TypeError,
    AttributeError,
    KeyError,
    IndexError,
    OSError,
    ImportError,
    LookupError,
)


class PhaseOrchestrator:
    """
    Orchestrator for coordinating lifecycle phase execution.

    This class is responsible for:
    1. Creating phase instances (dependency injection)
    2. Initializing runtime components (Worker, EventBus, etc.)
    3. Creating phase-specific contexts
    4. Executing phases in the correct order
    5. Assembling the final transaction result
    6. Cleanup

    Attributes:
        state: TransactionState instance shared across all phases
        output_path: Optional output path for export
    """

    def __init__(self, state: TransactionState, output_path: Optional[Path] = None):
        """
        Initialize the orchestrator.

        Args:
            state: TransactionState instance
            output_path: Optional output path for graph export
        """
        self.state = state
        self.output_path = output_path
        self._phases = self._create_phases()

    def _create_phases(self) -> Dict[LifecyclePhase, BasePhase]:
        """
        Create all 7 phase instances.

        This is the factory method that instantiates each phase class
        and injects the shared TransactionState.

        Returns:
            Dictionary mapping LifecyclePhase enum to phase instances
        """
        return {
            LifecyclePhase.ACQUIRE: AcquirePhase(self.state),
            LifecyclePhase.DETECT: DetectPhase(self.state),
            LifecyclePhase.PARSE: ParsePhase(self.state),
            LifecyclePhase.RESOLVE_DEPS: ResolveDepsPhase(self.state),
            LifecyclePhase.JOIN: JoinPhase(self.state),
            LifecyclePhase.ANALYZE: AnalyzePhase(self.state),
            LifecyclePhase.EXPORT: ExportPhase(self.state),
        }

    def run_all_phases(self) -> TransactionResult:
        """
        Execute all phases in order and return the result.

        This is the main entry point for transaction execution.

        Returns:
            TransactionResult with execution details

        Flow:
            1. Log ecosystem info
            2. Start progress manager
            3. Initialize runtime components (Worker, EventBus)
            4. Execute all 7 phases in order
            5. Flush graph to disk
            6. Register graph in GraphRegistry
            7. Assemble and return TransactionResult
        """
        # Log available ecosystems
        registry = EcosystemRegistry.get_instance()
        ecosystems = registry.list_ecosystems()
        logger.info(
            "[PID %d] Available ecosystems in this process: %s",
            os.getpid(),
            ecosystems,
        )

        # Start timer
        self.state.start_time = time.time()
        logger.info(
            "[PID %d] Starting transaction %s for: %s",
            os.getpid(),
            self.state.transaction_id,
            self.state.source,
        )

        # Start display manager
        if self.state.display_manager:
            self.state.display_manager.start_transaction(
                graph_id=self.state.graph_id or "unknown",
                source=self.state.source,
            )

        try:
            # Initialize runtime components
            self._initialize_runtime_components()

            # Execute all phases in order
            for phase_enum in LifecyclePhase:
                phase_start = time.time()
                phase = self._phases[phase_enum]
                context = self._create_context_for_phase(phase_enum)
                phase.run(context)
                phase_duration = time.time() - phase_start
                
                # Structured log for telemetry
                logger.info(
                    "Phase complete: %s (duration_ms=%d)",
                    phase_enum.name,
                    int(phase_duration * 1000)
                )
                self.state.update_phase_result(phase_enum, "duration_seconds", phase_duration)

                # Update metrics after each phase for real-time display
                self._update_progress_metrics()

            # Flush graph to disk
            cache_path = self._flush_graph_to_disk()

            # Register graph
            self._register_graph(cache_path)

            # Update progress metrics
            self._update_progress_metrics()

            # Return success result
            return self._create_success_result()

        except _SAFE_EXCEPTIONS as e:
            logger.error(
                "[PID %d] Transaction %s failed: %s",
                os.getpid(),
                self.state.transaction_id,
                e,
                exc_info=True,
            )
            return self._create_error_result(e)

        finally:
            # Cleanup (if needed)
            self._cleanup()

    def _initialize_runtime_components(self) -> None:
        """
        Initialize runtime components (Worker, EventBus, etc.).

        This method sets up the components needed for phase execution.
        """
        # Initialize Worker
        if self.state.worker is None:
            self.state.worker = Worker(max_workers=self.state.max_workers)
            logger.debug("Worker initialized with %d workers", self.state.max_workers)

        # Initialize EventBus and DependencyCollector
        if self.state.eventbus is None:
            self.state.eventbus = EventBus()
            logger.debug("EventBus initialized")

        if self.state.dependency_collector is None:
            self.state.dependency_collector = DependencyCollector(self.state.eventbus)
            logger.debug("DependencyCollector initialized")

        # Initialize ContractRegistry (scoped to transaction)
        if self.state.contract_registry is None:
            self.state.contract_registry = ContractRegistry()
            logger.debug("ContractRegistry initialized")

    def _create_context_for_phase(
        self, phase: LifecyclePhase
    ) -> TransactionContext:
        """
        Create phase-specific context.

        This is the context factory method that assembles the appropriate
        context for each phase based on the current state.

        Args:
            phase: The lifecycle phase to create context for

        Returns:
            Phase-specific TransactionContext instance

        Note:
            This method will be fully implemented in step 13.
            For now, it creates a basic TransactionContext.
        """
        # Create base context (will be enhanced in step 13)
        context_kwargs = dict(
            transaction_id=self.state.transaction_id,
            graph_id=self.state.graph_id,
            source=self.state.source,
            workspace=self.state.workspace,
            graph=self.state.graph_manager,
            graph_build_config=self.state.graph_build_config,
            eventbus=self.state.eventbus,
            contract_registry=self.state.contract_registry,
            display_manager=self.state.display_manager,
            enable_dependency_resolution=self.state.enable_dependency_resolution,
            max_dependency_depth=self.state.max_dependency_depth,
            max_dependencies=self.state.max_dependencies,
            current_phase=phase,
        )
        if phase == LifecyclePhase.EXPORT:
            return ExportContext(**context_kwargs, output_path=self.output_path)
        return TransactionContext(**context_kwargs)

    def _flush_graph_to_disk(self) -> Optional[Path]:
        """
        Get cached graph path from ExportPhase results.

        Returns:
            Path to cached graph file, or None if export didn't run

        Note:
            ExportPhase stores the cache path in state.graph_cache_path
        """
        return self.state.get_phase_result('graph_cache_path', None)

    def _register_graph(self, cache_path: Optional[Path]) -> None:
        """
        Register graph in GraphRegistry.

        Args:
            cache_path: Path to cached graph file
        """
        if cache_path and self.state.graph_manager:
            logger.info("Graph cached at: %s", cache_path)
            registry = GraphRegistry.get_instance(
                cache_root=self.state.graph_cache_root
            )
            summary = {
                "node_count": self.state.graph_manager.node_count(),
                "edge_count": self.state.graph_manager.edge_count(),
                "source": self.state.source,
                "transaction_id": self.state.transaction_id,
            }
            registry.register(
                graph_id=self.state.graph_id,
                cache_path=cache_path,
                summary=summary,
            )
            logger.info("Registered graph %s in GraphRegistry", self.state.graph_id)

    def _update_progress_metrics(self) -> None:
        """Update display manager with final metrics."""
        if self.state.display_manager and self.state.graph_manager:
            self.state.display_manager.update_metrics(
                node_count=self.state.graph_manager.node_count(),
                edge_count=self.state.graph_manager.edge_count(),
            )

    def _create_success_result(self) -> TransactionResult:
        """
        Create a successful TransactionResult.

        Returns:
            TransactionResult indicating success
        """
        elapsed = time.time() - self.state.start_time

        # Ensure graph_manager exists (create empty one if needed)
        if not self.state.graph_manager:
            path_namespace = None
            if self.state.parent_transaction_id:
                try:
                    if self.state.workspace and self.state.workspace.root_path:
                        path_namespace = Path(self.state.workspace.root_path).name
                except _SAFE_EXCEPTIONS:
                    path_namespace = None

            self.state.graph_manager = GraphManager(
                root_path=(
                    self.state.workspace.root_path if self.state.workspace else None
                ),
                path_namespace=path_namespace,
            )

        result = TransactionResult(
            transaction_id=self.state.transaction_id,
            graph_id=self.state.graph_id or "unknown",
            success=True,
            node_count=(
                self.state.graph_manager.node_count()
                if self.state.graph_manager
                else 0
            ),
            edge_count=(
                self.state.graph_manager.edge_count()
                if self.state.graph_manager
                else 0
            ),
            execution_time=elapsed,
            parent_transaction_id=self.state.parent_transaction_id,
        )

        logger.info(
            "[PID %d] Transaction %s completed in %.2fs",
            os.getpid(),
            self.state.transaction_id,
            elapsed,
        )

        return result

    def _create_error_result(self, error: Exception) -> TransactionResult:
        """
        Create an error TransactionResult.

        Args:
            error: The exception that caused the failure

        Returns:
            TransactionResult indicating failure
        """
        elapsed = time.time() - self.state.start_time

        return TransactionResult(
            transaction_id=self.state.transaction_id,
            graph_id=self.state.graph_id or "unknown",
            success=False,
            node_count=0,
            edge_count=0,
            execution_time=elapsed,
            error=str(error),
            parent_transaction_id=self.state.parent_transaction_id,
        )

    def _cleanup(self) -> None:
        """
        Cleanup resources (if needed).

        This method is called in the finally block of run_all_phases().
        """
        pass
