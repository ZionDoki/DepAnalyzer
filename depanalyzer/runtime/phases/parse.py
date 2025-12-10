"""
PARSE phase implementation.

Two-stage parsing with pipeline parallelism:
1. Config parsing (package.json, pom.xml, etc.) via Worker thread pool
2. Code parsing (source files) via GlobalTaskPool process pool

The two stages run in parallel using a producer-consumer pattern:
- Config parsing produces code files as they are discovered
- Code parsing consumes and processes files incrementally
"""

import logging
import queue
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from depanalyzer.graph import GraphManager
from depanalyzer.graph.contract_registry import ContractRegistry
from depanalyzer.parsers.registry import EcosystemRegistry
from depanalyzer.runtime.dependency_collector import DependencyCollector
from depanalyzer.runtime.lifecycle import LifecyclePhase
from depanalyzer.runtime.phases.base import BasePhase
from depanalyzer.runtime.context import TransactionContext
from depanalyzer.runtime.policies import CodeDependencyContext, CodeDependencyMapper
from depanalyzer.runtime.worker import Task, TaskPriority
from depanalyzer.runtime.task_pool import GlobalTaskPool
from depanalyzer.runtime.task_types import Task as PoolTask, TaskType, create_task

logger = logging.getLogger("depanalyzer.transaction.phase.parse")

_SAFE_EXCEPTIONS = (RuntimeError, ValueError, TypeError, AttributeError, KeyError,
                    IndexError, OSError, ImportError, LookupError)


class ParsePhase(BasePhase):
    """PARSE phase: Parse config files and source code."""

    IS_CRITICAL = True

    def __init__(self, state) -> None:
        """Initialize ParsePhase with thread-safe graph lock."""
        super().__init__(state)
        # Lock for thread-safe graph operations
        self._graph_lock = threading.Lock()

    def execute(self, context: TransactionContext) -> None:
        """Execute two-stage parsing with pipeline parallelism.

        Config parsing and code parsing run concurrently using a producer-consumer
        pattern. As config parsing discovers code files, they are immediately
        submitted to the code parser pool.
        """
        # Initialize GraphManager
        self._initialize_graph_manager()

        # Initialize DependencyCollector
        if self.state.dependency_collector is None:
            self.state.dependency_collector = DependencyCollector(self.state.eventbus)
            logger.info("Initialized DependencyCollector")

        detected_targets = self.state.detected_targets
        if not detected_targets:
            logger.info("No targets detected, skipping parse phase")
            return

        registry = EcosystemRegistry.get_instance()
        total_targets = sum(len(targets) for targets in detected_targets.values())

        logger.info("=== Pipeline Parsing (%d targets) ===", total_targets)

        # Use pipeline parallelism: config parsing produces code files,
        # code parsing consumes them concurrently
        self._execute_pipeline(registry, detected_targets)

        logger.info(
            "Parse phase completed: graph has %d nodes, %d edges",
            self.state.graph_manager.node_count(),
            self.state.graph_manager.edge_count(),
        )

    def _execute_pipeline(
        self,
        registry: EcosystemRegistry,
        detected_targets: Dict[str, List[Path]]
    ) -> None:
        """Execute config and code parsing in parallel pipeline.

        Args:
            registry: Ecosystem registry for parser lookup.
            detected_targets: Detected targets by ecosystem.
        """
        # Queue for passing code files from config parsing to code parsing
        # Items are (ecosystem, List[Path]) tuples, None signals completion
        code_file_queue: queue.Queue[Optional[Tuple[str, List[Path]]]] = queue.Queue()

        # Event to signal config parsing completion
        config_parsing_complete = threading.Event()

        # Statistics
        stats = {"config_success": 0, "config_failed": 0, "code_files_discovered": 0}
        stats_lock = threading.Lock()

        # Start code parsing consumer thread (non-daemon for proper cleanup)
        consumer_error: List[Exception] = []
        shutdown_event = threading.Event()  # Signal for graceful shutdown
        consumer_thread = threading.Thread(
            target=self._code_parser_consumer,
            args=(code_file_queue, config_parsing_complete, consumer_error, shutdown_event),
            name="CodeParserConsumer",
            daemon=False,  # Non-daemon to ensure proper cleanup
        )
        consumer_thread.start()

        # Config parsing (producer) - runs in main thread using Worker
        try:
            self._parse_configs_streaming(
                registry, detected_targets, code_file_queue, stats, stats_lock
            )
        finally:
            # Signal completion to consumer
            config_parsing_complete.set()
            code_file_queue.put(None)  # Sentinel value

        # Wait for code parsing to complete
        consumer_thread.join(timeout=600)  # 10 minute timeout
        if consumer_thread.is_alive():
            logger.error("Code parser consumer thread timed out, requesting shutdown")
            shutdown_event.set()  # Request graceful shutdown
            consumer_thread.join(timeout=10)  # Give it 10 more seconds
            if consumer_thread.is_alive():
                logger.error("Consumer thread still alive after shutdown request")

        # Check for consumer errors
        if consumer_error:
            logger.error("Code parser consumer encountered error: %s", consumer_error[0])

        with stats_lock:
            logger.info(
                "Pipeline completed: %d configs parsed (%d failed), %d code files discovered",
                stats["config_success"],
                stats["config_failed"],
                stats["code_files_discovered"],
            )

    def _initialize_graph_manager(self) -> None:
        """Initialize GraphManager if not already done."""
        if self.state.graph_manager is None:
            path_namespace = None
            if self.state.parent_transaction_id:
                try:
                    if self.state.workspace and self.state.workspace.root_path:
                        path_namespace = Path(self.state.workspace.root_path).name
                except _SAFE_EXCEPTIONS:
                    path_namespace = None

            self.state.graph_manager = GraphManager(
                graph_id=self.state.graph_id,
                root_path=self.state.workspace.root_path if self.state.workspace else None,
                path_namespace=path_namespace,
            )
            initial_metadata = getattr(self.state, "graph_metadata", {}) or {}
            if initial_metadata:
                for key, value in initial_metadata.items():
                    try:
                        self.state.graph_manager.set_metadata(key, value)
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.debug(
                            "Failed to seed graph metadata key %s: %s", key, exc
                        )
            logger.info(
                "Initialized GraphManager (graph_id=%s, namespace=%s)",
                self.state.graph_id, path_namespace
            )

    def _parse_configs_streaming(
        self,
        registry: EcosystemRegistry,
        detected_targets: Dict[str, List[Path]],
        code_file_queue: "queue.Queue[Optional[Tuple[str, List[Path]]]]",
        stats: Dict[str, int],
        stats_lock: threading.Lock,
    ) -> None:
        """Parse configs and stream discovered code files to queue.

        This is the producer side of the pipeline. As each config is parsed,
        discovered code files are immediately pushed to the queue for the
        consumer to process.

        Args:
            registry: Ecosystem registry for parser lookup.
            detected_targets: Detected targets by ecosystem.
            code_file_queue: Queue to push discovered code files.
            stats: Statistics dictionary to update.
            stats_lock: Lock for thread-safe stats updates.
        """
        self.state.worker.clear()

        for ecosystem, targets in detected_targets.items():
            parser_class = registry.get_parser(ecosystem)
            if parser_class is None:
                logger.warning("No parser for ecosystem: %s", ecosystem)
                continue

            logger.info("Creating parse tasks for %s: %d targets", ecosystem, len(targets))

            for target_path in targets:
                # Create task that pushes results to queue immediately
                def parse_task(
                    eco=ecosystem,
                    p_cls=parser_class,
                    tgt=target_path,
                    q=code_file_queue,
                    st=stats,
                    st_lock=stats_lock,
                ):
                    try:
                        parser_cfg = self.state.graph_build_config.get_parser_config(eco)
                        parser = p_cls(
                            self.state.workspace.root_path,
                            self.state.graph_manager,
                            self.state.eventbus,
                            config=parser_cfg,
                            contract_registry=self.state.contract_registry,
                        )
                        parser.parse(tgt)
                        code_files = parser.discover_code_files()

                        # Push code files to queue immediately for parallel processing
                        if code_files:
                            q.put((eco, code_files))
                            with st_lock:
                                st["code_files_discovered"] += len(code_files)

                        with st_lock:
                            st["config_success"] += 1

                        return {"ecosystem": eco, "success": True, "code_file_count": len(code_files)}

                    except _SAFE_EXCEPTIONS as e:
                        logger.error("Parser %s failed for %s: %s", eco, tgt, e, exc_info=True)
                        with st_lock:
                            st["config_failed"] += 1
                        return {"ecosystem": eco, "success": False, "error": str(e)}

                try:
                    rel_path = target_path.relative_to(self.state.workspace.root_path)
                    task_suffix = str(rel_path).replace("\\", "/")
                except ValueError:
                    task_suffix = str(target_path).replace("\\", "/")

                task = Task(
                    task_id=f"parse_{ecosystem}_{task_suffix}",
                    func=parse_task,
                    priority=TaskPriority.NORMAL,
                )
                self.state.worker.enqueue(task)

        # Execute all config parsing tasks
        # Results are pushed to queue as they complete
        self.state.worker.run_all()

    def _code_parser_consumer(
        self,
        code_file_queue: "queue.Queue[Optional[Tuple[str, List[Path]]]]",
        config_complete: threading.Event,
        error_list: List[Exception],
        shutdown_event: threading.Event,
    ) -> None:
        """Consumer thread that processes code files from the queue.

        This runs concurrently with config parsing, processing code files
        as they are discovered.

        Args:
            code_file_queue: Queue to receive code files from.
            config_complete: Event signaling config parsing is done.
            error_list: List to store any exceptions for the main thread.
            shutdown_event: Event signaling graceful shutdown request.
        """
        # Use GlobalTaskPool instead of CodeParserPool
        task_pool = GlobalTaskPool.get_instance(self.state.max_workers)
        task_futures: Dict[str, Tuple[Path, Any]] = {}  # task_id -> (file_path, future)
        successful, skipped, failed = 0, 0, 0

        try:
            # Collect futures as code files arrive
            while not shutdown_event.is_set():
                try:
                    # Use timeout to periodically check events
                    item = code_file_queue.get(timeout=0.5)

                    if item is None:
                        # Sentinel value - config parsing is complete
                        break

                    ecosystem, file_paths = item
                    if file_paths:
                        logger.debug(
                            "Consumer received %d code files for %s",
                            len(file_paths), ecosystem
                        )
                        code_cfg = self.state.graph_build_config.get_code_parser_config(ecosystem)

                        # Submit each file as a PARSE_CODE task
                        for file_path in file_paths:
                            if shutdown_event.is_set():
                                break
                            task = create_task(
                                TaskType.PARSE_CODE,
                                payload={
                                    "file_path": str(file_path),
                                    "ecosystem": ecosystem,
                                    "config": code_cfg,
                                },
                            )
                            future = task_pool.submit(task)
                            task_futures[task.task_id] = (file_path, future)

                except queue.Empty:
                    # Only exit if config is complete AND we got the sentinel (None)
                    # This avoids race condition where queue appears empty but items are being added
                    if config_complete.is_set():
                        # Drain any remaining items before exiting
                        try:
                            while True:
                                item = code_file_queue.get_nowait()
                                if item is None:
                                    break
                                ecosystem, file_paths = item
                                if file_paths:
                                    code_cfg = self.state.graph_build_config.get_code_parser_config(ecosystem)
                                    for file_path in file_paths:
                                        task = create_task(
                                            TaskType.PARSE_CODE,
                                            payload={
                                                "file_path": str(file_path),
                                                "ecosystem": ecosystem,
                                                "config": code_cfg,
                                            },
                                        )
                                        future = task_pool.submit(task)
                                        task_futures[task.task_id] = (file_path, future)
                        except queue.Empty:
                            pass
                        break
                    continue

            # Wait for all code parsing to complete
            if task_futures:
                logger.info("Waiting for %d code files to be parsed", len(task_futures))

                # Collect results from futures
                for task_id, (file_path, future) in task_futures.items():
                    try:
                        result_wrapper = future.result(timeout=60.0)
                        if result_wrapper is None:
                            failed += 1
                            logger.debug("Parse task returned None for %s", file_path)
                            continue
                        parse_result = result_wrapper.get("result", {}) if result_wrapper.get("success") else {}

                        if not result_wrapper.get("success"):
                            failed += 1
                            logger.debug("Parse task failed for %s: %s", file_path, result_wrapper.get("error"))
                        elif parse_result.get("skipped"):
                            skipped += 1
                        elif parse_result.get("error"):
                            failed += 1
                        else:
                            self._process_code_parse_result(file_path, parse_result)
                            successful += 1

                    except Exception as e:
                        failed += 1
                        logger.debug("Failed to get result for %s: %s", file_path, e)

            logger.info(
                "Code parsing completed: %d ok, %d skipped, %d failed",
                successful, skipped, failed
            )

        except Exception as e:
            logger.exception("Code parser consumer failed")
            error_list.append(e)

    def _parse_configs(
        self,
        registry: EcosystemRegistry,
        detected_targets: Dict[str, List[Path]]
    ) -> Dict[str, List[Path]]:
        """Stage 1: Parse config files and discover code files."""
        self.state.worker.clear()
        code_files_to_parse: Dict[str, List[Path]] = {}

        for ecosystem, targets in detected_targets.items():
            parser_class = registry.get_parser(ecosystem)
            if parser_class is None:
                logger.warning("No parser for ecosystem: %s", ecosystem)
                continue

            logger.info("Creating parse tasks for %s: %d targets", ecosystem, len(targets))

            for target_path in targets:
                def parse_task(eco=ecosystem, p_cls=parser_class, tgt=target_path):
                    try:
                        parser_cfg = self.state.graph_build_config.get_parser_config(eco)
                        parser = p_cls(
                            self.state.workspace.root_path,
                            self.state.graph_manager,
                            self.state.eventbus,
                            config=parser_cfg,
                            contract_registry=self.state.contract_registry,
                        )
                        parser.parse(tgt)
                        code_files = parser.discover_code_files()
                        return {
                            "ecosystem": eco,
                            "code_files": code_files,
                            "success": True,
                        }
                    except _SAFE_EXCEPTIONS as e:
                        logger.error("Parser %s failed for %s: %s", eco, tgt, e)
                        return {"ecosystem": eco, "code_files": [], "success": False}

                try:
                    rel_path = target_path.relative_to(self.state.workspace.root_path)
                    task_suffix = str(rel_path).replace("\\", "/")
                except ValueError:
                    task_suffix = str(target_path).replace("\\", "/")

                task = Task(
                    task_id=f"parse_{ecosystem}_{task_suffix}",
                    func=parse_task,
                    priority=TaskPriority.NORMAL,
                )
                self.state.worker.enqueue(task)

        # Execute all config parsing tasks
        results = self.state.worker.run_all()

        # Collect discovered code files
        for _task_id, result in results.items():
            if result.success and result.result:
                ecosystem = result.result.get("ecosystem")
                code_files = result.result.get("code_files", [])
                if ecosystem and code_files:
                    if ecosystem not in code_files_to_parse:
                        code_files_to_parse[ecosystem] = []
                    code_files_to_parse[ecosystem].extend(code_files)

        return code_files_to_parse

    def _parse_code_files(self, code_files_to_parse: Dict[str, List[Path]]) -> None:
        """Stage 2: Parse code files via GlobalTaskPool."""
        task_pool = GlobalTaskPool.get_instance(self.state.max_workers)
        task_futures: Dict[str, Tuple[Path, Any]] = {}  # task_id -> (file_path, future)

        for ecosystem, file_paths in code_files_to_parse.items():
            logger.info("Submitting %d code files for %s", len(file_paths), ecosystem)
            code_cfg = self.state.graph_build_config.get_code_parser_config(ecosystem)

            # Submit each file as a PARSE_CODE task
            for file_path in file_paths:
                task = create_task(
                    TaskType.PARSE_CODE,
                    payload={
                        "file_path": str(file_path),
                        "ecosystem": ecosystem,
                        "config": code_cfg,
                    },
                )
                future = task_pool.submit(task)
                task_futures[task.task_id] = (file_path, future)

        logger.info("Waiting for %d code files to be parsed", len(task_futures))

        # Process results
        successful, skipped, failed = 0, 0, 0
        for task_id, (file_path, future) in task_futures.items():
            try:
                result_wrapper = future.result(timeout=60.0)
                if result_wrapper is None:
                    failed += 1
                    logger.debug("Parse task returned None for %s", file_path)
                    continue
                parse_result = result_wrapper.get("result", {}) if result_wrapper.get("success") else {}

                if not result_wrapper.get("success"):
                    failed += 1
                    logger.debug("Parse task failed for %s: %s", file_path, result_wrapper.get("error"))
                elif parse_result.get("skipped"):
                    skipped += 1
                elif parse_result.get("error"):
                    failed += 1
                else:
                    self._process_code_parse_result(file_path, parse_result)
                    successful += 1

            except Exception as e:
                failed += 1
                logger.debug("Failed to get result for %s: %s", file_path, e)

        logger.info(
            "Stage 2 completed: %d ok, %d skipped, %d failed",
            successful,
            skipped,
            failed,
        )

    def _process_code_parse_result(self, file_path: Path, parse_result: Dict[str, Any]) -> None:
        """Process code parse result via CodeDependencyMapper.

        Thread-safe: uses _graph_lock to protect graph modifications.
        """
        if not self.state.graph_manager:
            return

        # Create context for mapper
        tx_ctx = TransactionContext(
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
            current_phase=LifecyclePhase.PARSE,
        )

        code_ctx = CodeDependencyContext(
            transaction_ctx=tx_ctx,
            file_path=file_path,
            parse_result=parse_result,
        )

        ecosystem = parse_result.get("ecosystem")
        mapper: CodeDependencyMapper
        if ecosystem and ecosystem in self.state.code_dependency_mappers:
            mapper = self.state.code_dependency_mappers[ecosystem]
        else:
            mapper = self.state.default_code_dependency_mapper

        # Thread-safe graph modification
        with self._graph_lock:
            try:
                mapper.map(code_ctx)
                # Record file processed for stats
                if self.state.display_manager:
                    self.state.display_manager.stats_collector.record_file_processed()
            except _SAFE_EXCEPTIONS:
                logger.exception("CodeDependencyMapper failed for %s", file_path)
