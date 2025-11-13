"""Core transaction for single scan/build session.

Transaction drives the full lifecycle (Acquire→Detect→Parse→ResolveDeps→Join→Analyze→Export),
maintains a single GraphManager instance, and coordinates with the global registry.
"""

import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

from runtime.eventbus import EventBus
from runtime.lifecycle import LifecyclePhase
from runtime.worker import Task, TaskPriority, Worker
from runtime.workspace import Workspace

logger = logging.getLogger("depanalyzer.transaction")


class Transaction:
    """Core transaction for a single analysis session.

    A transaction:
    - Receives input (local path or Git URL)
    - Drives the full lifecycle
    - Maintains a single GraphManager instance
    - Interacts with the global registry
    - Can spawn new third-party transactions
    """

    def __init__(
        self,
        source: str,
        graph_id: Optional[str] = None,
        max_workers: int = 8,
        max_dependency_depth: int = 3,
        parent_transaction_id: Optional[str] = None,
    ) -> None:
        """Initialize transaction.

        Args:
            source: Local path or Git URL.
            graph_id: Optional graph identifier. If None, generated from source.
            max_workers: Maximum concurrent workers.
            max_dependency_depth: Maximum third-party dependency depth.
            parent_transaction_id: Optional parent transaction ID for third-party deps.
        """
        self.source = source
        self.graph_id = graph_id
        self.max_workers = max_workers
        self.max_dependency_depth = max_dependency_depth
        self.parent_transaction_id = parent_transaction_id

        # Core components
        self.workspace = Workspace(source)
        self.worker = Worker(max_workers=max_workers)
        self.eventbus = EventBus()

        # Will be initialized during run
        self._graph_manager = None
        self._current_phase = None
        self._start_time = 0.0

        logger.info("Transaction initialized for source: %s", source)
        if parent_transaction_id:
            logger.info("  Parent transaction: %s", parent_transaction_id)

    def _phase_acquire(self) -> None:
        """Phase A: Acquire source code."""
        logger.info("=== Phase: %s ===", LifecyclePhase.ACQUIRE)
        self._current_phase = LifecyclePhase.ACQUIRE

        root_path = self.workspace.acquire()
        logger.info("Workspace acquired at: %s", root_path)

        # Generate graph_id if not provided
        if not self.graph_id:
            self.graph_id = f"graph_{self.workspace.get_signature()}"
            logger.info("Generated graph_id: %s", self.graph_id)

    def _phase_detect(self) -> None:
        """Phase B: Parallel detection of targets."""
        logger.info("=== Phase: %s ===", LifecyclePhase.DETECT)
        self._current_phase = LifecyclePhase.DETECT

        # TODO: Implement detector discovery and execution
        # For now, placeholder
        logger.info("Detection phase not yet implemented")

    def _phase_parse(self) -> None:
        """Phase C: Parallel parsing of detected targets."""
        logger.info("=== Phase: %s ===", LifecyclePhase.PARSE)
        self._current_phase = LifecyclePhase.PARSE

        # TODO: Implement parser execution
        logger.info("Parse phase not yet implemented")

    def _phase_resolve_deps(self) -> None:
        """Phase D: Unified dependency resolution."""
        logger.info("=== Phase: %s ===", LifecyclePhase.RESOLVE_DEPS)
        self._current_phase = LifecyclePhase.RESOLVE_DEPS

        # TODO: Implement dependency resolution
        logger.info("Dependency resolution phase not yet implemented")

    def _phase_join(self) -> None:
        """Phase E: Hook execution for cross-domain associations."""
        logger.info("=== Phase: %s ===", LifecyclePhase.JOIN)
        self._current_phase = LifecyclePhase.JOIN

        # TODO: Implement hook execution
        logger.info("Join phase not yet implemented")

    def _phase_analyze(self) -> None:
        """Phase F: Analysis on transaction graph."""
        logger.info("=== Phase: %s ===", LifecyclePhase.ANALYZE)
        self._current_phase = LifecyclePhase.ANALYZE

        # TODO: Implement analysis
        logger.info("Analysis phase not yet implemented")

    def _phase_export(self, output_path: Optional[Path] = None) -> None:
        """Phase G: Export results.

        Args:
            output_path: Optional output path for results.
        """
        logger.info("=== Phase: %s ===", LifecyclePhase.EXPORT)
        self._current_phase = LifecyclePhase.EXPORT

        # TODO: Implement export
        if output_path:
            logger.info("Export to: %s", output_path)
        logger.info("Export phase not yet implemented")

    def run(self, output_path: Optional[Path] = None) -> "GraphManager":
        """Execute full transaction lifecycle.

        Args:
            output_path: Optional output path for export phase.

        Returns:
            GraphManager: Transaction graph manager.
        """
        self._start_time = time.time()
        logger.info("Starting transaction for: %s", self.source)

        try:
            # Execute phases in order
            self._phase_acquire()
            self._phase_detect()
            self._phase_parse()
            self._phase_resolve_deps()
            self._phase_join()
            self._phase_analyze()
            self._phase_export(output_path)

            elapsed = time.time() - self._start_time
            logger.info("Transaction completed in %.2fs", elapsed)

            # Import here to avoid circular dependency
            from utils.graph import GraphManager

            # Return graph manager (will be properly initialized in later implementation)
            if not self._graph_manager:
                self._graph_manager = GraphManager()

            return self._graph_manager

        except Exception as e:
            elapsed = time.time() - self._start_time
            logger.error("Transaction failed after %.2fs: %s", elapsed, e)
            raise

    def get_current_phase(self) -> Optional[LifecyclePhase]:
        """Get current execution phase.

        Returns:
            Optional[LifecyclePhase]: Current phase or None if not started.
        """
        return self._current_phase

    def get_graph_manager(self) -> Optional["GraphManager"]:
        """Get transaction graph manager.

        Returns:
            Optional[GraphManager]: Graph manager or None if not initialized.
        """
        return self._graph_manager
