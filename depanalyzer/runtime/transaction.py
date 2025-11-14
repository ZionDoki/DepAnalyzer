"""Core transaction for single scan/build session.

Transaction drives the full lifecycle (Acquire→Detect→Parse→ResolveDeps→Join→Analyze→Export),
maintains a single GraphManager instance, and coordinates with the global registry.

Supports multi-process execution via serialization.
"""

import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from depanalyzer.runtime.eventbus import EventBus
from depanalyzer.runtime.lifecycle import LifecyclePhase
from depanalyzer.runtime.worker import Task, TaskPriority, Worker
from depanalyzer.runtime.workspace import Workspace

if TYPE_CHECKING:
    from depanalyzer.runtime.coordinator import TransactionResult

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
        transaction_id: Optional[str] = None,
    ) -> None:
        """Initialize transaction.

        Args:
            source: Local path or Git URL.
            graph_id: Optional graph identifier. If None, generated from source.
            max_workers: Maximum concurrent workers.
            max_dependency_depth: Maximum third-party dependency depth.
            parent_transaction_id: Optional parent transaction ID for third-party deps.
            transaction_id: Optional transaction identifier. If None, generated.
        """
        self.transaction_id = transaction_id or str(uuid.uuid4())
        self.source = source
        self.graph_id = graph_id
        self.max_workers = max_workers
        self.max_dependency_depth = max_dependency_depth
        self.parent_transaction_id = parent_transaction_id

        # Core components (will be initialized in __setstate__ for deserialization)
        self.workspace = Workspace(source)
        self.worker: Optional[Worker] = None
        self.eventbus: Optional[EventBus] = None

        # Will be initialized during run
        self._graph_manager = None
        self._current_phase = None
        self._start_time = 0.0

        logger.info("Transaction %s initialized for source: %s", self.transaction_id, source)
        if parent_transaction_id:
            logger.info("  Parent transaction: %s", parent_transaction_id)

    def __getstate__(self) -> dict:
        """Prepare transaction for pickling (serialization).

        Exclude non-serializable objects like Worker and EventBus.
        They will be recreated in the subprocess.

        Returns:
            dict: Serializable state.
        """
        state = self.__dict__.copy()
        # Remove non-serializable objects
        state["worker"] = None
        state["eventbus"] = None
        state["_graph_manager"] = None
        return state

    def __setstate__(self, state: dict) -> None:
        """Restore transaction from pickled state.

        Recreate Worker and EventBus in the subprocess.

        Args:
            state: Pickled state.
        """
        self.__dict__.update(state)
        # Recreate process-local objects
        self.worker = Worker(max_workers=self.max_workers)
        self.eventbus = EventBus()
        logger.info(
            "Transaction %s deserialized in subprocess (PID: %d)",
            self.transaction_id,
            __import__("os").getpid(),
        )

    def _ensure_worker(self) -> Worker:
        """Ensure worker is initialized (for lazy initialization).

        Returns:
            Worker: Initialized worker instance.
        """
        if self.worker is None:
            self.worker = Worker(max_workers=self.max_workers)
        return self.worker

    def _ensure_eventbus(self) -> EventBus:
        """Ensure eventbus is initialized (for lazy initialization).

        Returns:
            EventBus: Initialized eventbus instance.
        """
        if self.eventbus is None:
            self.eventbus = EventBus()
        return self.eventbus

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

        from depanalyzer.parsers.registry import EcosystemRegistry

        registry = EcosystemRegistry.get_instance()

        # Get all registered detector classes
        ecosystems = registry.list_ecosystems()
        if not ecosystems:
            logger.warning("No ecosystems registered, skipping detection")
            return

        logger.info("Running detectors for %d ecosystems", len(ecosystems))

        # Store detected targets for parse phase
        self._detected_targets: Dict[str, List[Path]] = {}

        # Create detection tasks
        worker = self._ensure_worker()
        eventbus = self._ensure_eventbus()

        for ecosystem in ecosystems:
            detector_class = registry.get_detector(ecosystem)
            if detector_class is None:
                logger.debug("No detector registered for ecosystem: %s", ecosystem)
                continue

            # Create detector task
            def detect_task(eco=ecosystem, det_cls=detector_class):
                """Detection task wrapper."""
                try:
                    detector = det_cls(self.workspace.root_path, eventbus)
                    targets = detector.detect()
                    logger.info("Detector %s found %d targets", eco, len(targets))
                    return {"ecosystem": eco, "targets": targets, "success": True}
                except Exception as e:
                    logger.error("Detector %s failed: %s", eco, e)
                    return {"ecosystem": eco, "targets": [], "success": False, "error": str(e)}

            task = Task(
                task_id=f"detect_{ecosystem}",
                func=detect_task,
                priority=TaskPriority.HIGH,
            )
            worker.enqueue(task)

        # Execute all detection tasks and collect results
        results = worker.run_all()

        # Process results and store detected targets
        for task_id, result in results.items():
            if result.success and result.result:
                ecosystem = result.result.get("ecosystem")
                targets = result.result.get("targets", [])
                if ecosystem and targets:
                    self._detected_targets[ecosystem] = targets
                    logger.info("Stored %d targets for ecosystem: %s", len(targets), ecosystem)

        total_targets = sum(len(targets) for targets in self._detected_targets.values())
        logger.info("Detection phase completed: %d ecosystems, %d total targets",
                    len(self._detected_targets), total_targets)

    def _phase_parse(self) -> None:
        """Phase C: Parallel parsing of detected targets.

        Two-stage parsing:
        1. Config parsing (CMakeLists.txt, hvigorfile.ts, etc.) via Worker thread pool
        2. Code parsing (source files) via CodeParserPool process pool
        """
        logger.info("=== Phase: %s ===", LifecyclePhase.PARSE)
        self._current_phase = LifecyclePhase.PARSE

        # Initialize GraphManager if not already done
        if self._graph_manager is None:
            from depanalyzer.graph.manager import GraphManager
            self._graph_manager = GraphManager(
                graph_id=self.graph_id,
                root_path=self.workspace.root_path
            )
            logger.info("Initialized GraphManager with graph_id: %s, root_path: %s",
                        self.graph_id, self.workspace.root_path)

        if not hasattr(self, '_detected_targets') or not self._detected_targets:
            logger.info("No targets detected, skipping parse phase")
            return

        from depanalyzer.parsers.registry import EcosystemRegistry
        registry = EcosystemRegistry.get_instance()

        # Stage 1: Config parsing
        logger.info("=== Stage 1: Config Parsing ===")

        worker = self._ensure_worker()
        worker.clear()  # Clear previous results
        eventbus = self._ensure_eventbus()

        code_files_to_parse: Dict[str, List[Path]] = {}  # ecosystem -> [file paths]

        for ecosystem, targets in self._detected_targets.items():
            parser_class = registry.get_parser(ecosystem)
            if parser_class is None:
                logger.warning("No parser registered for ecosystem: %s", ecosystem)
                continue

            logger.info("Creating parse tasks for ecosystem %s: %d targets", ecosystem, len(targets))

            for target_path in targets:
                def parse_task(eco=ecosystem, p_cls=parser_class, tgt=target_path):
                    """Config parsing task wrapper."""
                    try:
                        parser = p_cls(self.workspace.root_path, self._graph_manager, eventbus)
                        parser.parse(tgt)

                        # Discover code files from config parser
                        code_files = parser.discover_code_files()

                        logger.info("Parser %s processed %s, discovered %d code files",
                                   eco, tgt.name, len(code_files))
                        return {
                            "ecosystem": eco,
                            "target": str(tgt),
                            "code_files": code_files,
                            "success": True
                        }
                    except Exception as e:
                        logger.error("Parser %s failed for %s: %s", eco, tgt, e)
                        return {
                            "ecosystem": eco,
                            "target": str(tgt),
                            "code_files": [],
                            "success": False,
                            "error": str(e)
                        }

                task = Task(
                    task_id=f"parse_{ecosystem}_{target_path.name}",
                    func=parse_task,
                    priority=TaskPriority.NORMAL,
                )
                worker.enqueue(task)

        # Execute all config parsing tasks
        results = worker.run_all()

        # Collect discovered code files
        for task_id, result in results.items():
            if result.success and result.result:
                ecosystem = result.result.get("ecosystem")
                code_files = result.result.get("code_files", [])
                if ecosystem and code_files:
                    if ecosystem not in code_files_to_parse:
                        code_files_to_parse[ecosystem] = []
                    code_files_to_parse[ecosystem].extend(code_files)

        total_config_success = sum(1 for r in results.values() if r.success)
        total_code_files = sum(len(files) for files in code_files_to_parse.values())
        logger.info("Stage 1 completed: %d/%d config files parsed, %d code files discovered",
                   total_config_success, len(results), total_code_files)

        # Stage 2: Code parsing
        if not code_files_to_parse:
            logger.info("No code files to parse, skipping Stage 2")
            return

        logger.info("=== Stage 2: Code Parsing ===")

        from depanalyzer.runtime.code_parser_pool import CodeParserPool
        from concurrent.futures import as_completed

        code_pool = CodeParserPool.get_instance()

        # Submit all code files to process pool
        futures_list = []
        for ecosystem, file_paths in code_files_to_parse.items():
            logger.info("Submitting %d code files for ecosystem: %s", len(file_paths), ecosystem)
            batch_futures = code_pool.submit_batch(file_paths, ecosystem)
            futures_list.extend(batch_futures)

        # Wait for code parsing to complete and collect results
        logger.info("Waiting for %d code files to be parsed", len(futures_list))

        code_results = code_pool.wait_for_completion(futures_list, timeout=300)

        # Process code parse results and update graph
        successful = 0
        skipped = 0
        failed = 0

        for file_path, parse_result in code_results.items():
            if parse_result.get("skipped"):
                skipped += 1
                continue
            elif parse_result.get("error"):
                failed += 1
                logger.warning("Code parsing failed for %s: %s",
                             file_path, parse_result.get("error"))
                continue

            # Add code file node and dependencies to graph
            self._process_code_parse_result(file_path, parse_result)
            successful += 1

        logger.info("Stage 2 completed: %d successful, %d skipped, %d failed",
                   successful, skipped, failed)
        logger.info("Parse phase completed: graph has %d nodes, %d edges",
                   self._graph_manager.node_count(), self._graph_manager.edge_count())

    def _process_code_parse_result(self, file_path: Path, parse_result: Dict[str, Any]) -> None:
        """Process code parse result and update graph.

        Args:
            file_path: Path to parsed code file.
            parse_result: Parse result dictionary with includes/imports.
        """
        if not self._graph_manager:
            return

        # Add file node
        file_node_id = str(file_path)
        self._graph_manager.add_node(
            file_node_id,
            "code_file",
            path=str(file_path),
            name=file_path.name
        )

        # Add include/import edges
        includes = parse_result.get("includes", [])
        for include_path in includes:
            include_node_id = str(include_path) if isinstance(include_path, Path) else include_path
            self._graph_manager.add_edge(
                file_node_id,
                include_node_id,
                "includes",
                path=include_node_id
            )

    def _phase_resolve_deps(self) -> None:
        """Phase D: Unified dependency resolution.

        Discovers dependencies and spawns child transactions in parallel.
        """
        logger.info("=== Phase: %s ===", LifecyclePhase.RESOLVE_DEPS)
        self._current_phase = LifecyclePhase.RESOLVE_DEPS

        # Check if we've reached max depth
        if self.max_dependency_depth <= 0:
            logger.info("Max dependency depth reached, skipping dependency resolution")
            return

        # Discover dependencies
        dependencies = self._discover_dependencies()

        if not dependencies:
            logger.info("No dependencies found")
            return

        logger.info("Found %d dependencies to resolve", len(dependencies))

        # Import coordinator
        from depanalyzer.runtime.coordinator import TransactionCoordinator

        # Get global coordinator instance
        coordinator = TransactionCoordinator.get_instance()

        # Submit child transactions to process pool
        futures = []
        for dep in dependencies:
            logger.info(
                "Creating child transaction for dependency: %s (parent: %s)",
                dep.get("name", "unknown"),
                self.transaction_id,
            )

            child_tx = Transaction(
                source=dep["source"],
                graph_id=None,  # Will be generated in child
                max_workers=self.max_workers,
                max_dependency_depth=self.max_dependency_depth - 1,
                parent_transaction_id=self.transaction_id,
            )

            future = coordinator.submit(child_tx)
            futures.append((dep, future, child_tx.transaction_id))

        # Wait for all child transactions to complete
        logger.info("Waiting for %d child transactions to complete", len(futures))

        from concurrent.futures import as_completed

        completed_count = 0
        failed_count = 0

        for dep, future, child_tx_id in futures:
            try:
                result = future.result(timeout=600)  # 10 minute timeout per child

                if result.success:
                    completed_count += 1
                    logger.info(
                        "Child transaction %s completed: %s (%d nodes, %d edges)",
                        child_tx_id,
                        dep.get("name", "unknown"),
                        result.node_count,
                        result.edge_count,
                    )

                    # Link child graph in registry
                    self._link_dependency_graph(result.graph_id, dep)
                else:
                    failed_count += 1
                    logger.error(
                        "Child transaction %s failed: %s - %s",
                        child_tx_id,
                        dep.get("name", "unknown"),
                        result.error,
                    )

            except Exception as e:
                failed_count += 1
                logger.error(
                    "Failed to get result for child transaction %s: %s", child_tx_id, e
                )

        logger.info(
            "Dependency resolution completed: %d succeeded, %d failed",
            completed_count,
            failed_count,
        )

    def _discover_dependencies(self) -> List[Dict[str, str]]:
        """Discover dependencies from the current graph.

        This is a placeholder that should be implemented based on the
        actual dependency discovery logic.

        Returns:
            List[Dict[str, str]]: List of dependency specifications.
                Each dict should have at minimum: {"name": "...", "source": "..."}
        """
        # TODO: Implement actual dependency discovery
        # This should analyze the parsed code and extract:
        # - npm/yarn dependencies from package.json
        # - Maven/Gradle dependencies from pom.xml/build.gradle
        # - pip dependencies from requirements.txt
        # - etc.

        logger.info("Dependency discovery not yet implemented")
        return []

    def _link_dependency_graph(self, child_graph_id: str, dep_info: Dict[str, str]) -> None:
        """Link a child dependency graph to the current graph.

        Args:
            child_graph_id: Graph ID of the child dependency.
            dep_info: Dependency information dict.
        """
        # TODO: Implement graph linking logic
        # This should create edges in the current graph pointing to the child graph
        # via the GraphRegistry

        logger.info(
            "Linking dependency graph %s to current graph %s (dep: %s)",
            child_graph_id,
            self.graph_id,
            dep_info.get("name", "unknown"),
        )

    def _phase_join(self) -> None:
        """Phase E: Hook execution for cross-domain associations.

        This phase executes all registered hooks to establish cross-language
        and cross-ecosystem connections (e.g., Hvigor ↔ CMake).
        """
        logger.info("=== Phase: %s ===", LifecyclePhase.JOIN)
        self._current_phase = LifecyclePhase.JOIN

        if not self._graph_manager:
            logger.warning("GraphManager not initialized, skipping join phase")
            return

        eventbus = self._ensure_eventbus()

        # Initialize and execute hooks
        hooks = []

        # HvigorCMakeBridge hook (legacy path-based matching)
        try:
            from depanalyzer.hooks.hvigor_cmake_bridge import HvigorCMakeBridge
            bridge = HvigorCMakeBridge(self._graph_manager, eventbus)
            hooks.append(("HvigorCMakeBridge", bridge))
        except ImportError as e:
            logger.debug("HvigorCMakeBridge not available: %s", e)

        # ContractMatcher hook (new contract-based matching)
        try:
            from depanalyzer.hooks.contract_matcher import ContractMatcher
            matcher = ContractMatcher(self._graph_manager, eventbus)
            hooks.append(("ContractMatcher", matcher))
        except ImportError as e:
            logger.warning("ContractMatcher not available: %s", e)

        # Execute all hooks
        for hook_name, hook in hooks:
            try:
                logger.info("Executing hook: %s", hook_name)
                hook.execute()
            except Exception as e:
                logger.error("Hook %s failed: %s", hook_name, e, exc_info=True)

        logger.info("Join phase completed with %d hooks executed", len(hooks))

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

    def run(self, output_path: Optional[Path] = None) -> "TransactionResult":
        """Execute full transaction lifecycle.

        Args:
            output_path: Optional output path for export phase.

        Returns:
            TransactionResult: Transaction execution result.
        """
        # Import here to avoid circular dependency
        from depanalyzer.runtime.coordinator import TransactionResult

        self._start_time = time.time()
        logger.info(
            "[PID %d] Starting transaction %s for: %s",
            os.getpid(),
            self.transaction_id,
            self.source,
        )

        try:
            # Ensure worker and eventbus are initialized
            self._ensure_worker()
            self._ensure_eventbus()

            # Execute phases in order
            self._phase_acquire()
            self._phase_detect()
            self._phase_parse()
            self._phase_resolve_deps()
            self._phase_join()
            self._phase_analyze()
            self._phase_export(output_path)

            elapsed = time.time() - self._start_time

            # Import here to avoid circular dependency
            from depanalyzer.graph.manager import GraphManager

            # Return graph manager (will be properly initialized in later implementation)
            if not self._graph_manager:
                self._graph_manager = GraphManager(
                    root_path=self.workspace.root_path
                )

            # Build result
            result = TransactionResult(
                transaction_id=self.transaction_id,
                graph_id=self.graph_id or "unknown",
                success=True,
                node_count=self._graph_manager.node_count() if self._graph_manager else 0,
                edge_count=self._graph_manager.edge_count() if self._graph_manager else 0,
                execution_time=elapsed,
                parent_transaction_id=self.parent_transaction_id,
            )

            logger.info(
                "[PID %d] Transaction %s completed in %.2fs",
                os.getpid(),
                self.transaction_id,
                elapsed,
            )

            return result

        except Exception as e:
            elapsed = time.time() - self._start_time
            logger.error(
                "[PID %d] Transaction %s failed after %.2fs: %s",
                os.getpid(),
                self.transaction_id,
                elapsed,
                e,
            )

            # Import here to avoid circular dependency
            from depanalyzer.runtime.coordinator import TransactionResult

            return TransactionResult(
                transaction_id=self.transaction_id,
                graph_id=self.graph_id or "unknown",
                success=False,
                node_count=0,
                edge_count=0,
                execution_time=elapsed,
                error=str(e),
                parent_transaction_id=self.parent_transaction_id,
            )

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
