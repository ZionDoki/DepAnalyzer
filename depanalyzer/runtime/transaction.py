"""Core transaction for single scan/build session.

Transaction drives the full lifecycle (Acquire→Detect→Parse→ResolveDeps→Join→Analyze→Export),
maintains a single GraphManager instance, and coordinates with the global registry.

Supports multi-process execution via serialization.
"""

# Transaction intentionally guards every lifecycle stage to report deterministic failures.


import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Sequence

from networkx import MultiDiGraph
from networkx.readwrite import node_link_data

import depanalyzer.parsers

from depanalyzer.analysis.deadcode import DeadcodeAnalyzer
from depanalyzer.analysis.linkage import LinkageAnalyzer
from depanalyzer.analysis.uncertainty import (
    UncertaintyAnalyzer,
    UncertaintyPolicyConfig,
)
from depanalyzer.graph.contract_registry import ContractRegistry
from depanalyzer.graph.global_dag import GlobalDAG
from depanalyzer.graph.linking import LinkClass
from depanalyzer.graph.linker_contract import GlobalContractLinker
from depanalyzer.graph.manager import EdgeKind, GraphManager, NodeType
from depanalyzer.graph.registry import GraphRegistry
from depanalyzer.graph.schema_utils import canonicalize_edge, canonicalize_node
from depanalyzer.parsers.base import DependencySpec
from depanalyzer.parsers.cpp.code_dependency_mapper import CppCodeDependencyMapper
from depanalyzer.parsers.hvigor.code_dependency_mapper import (
    HvigorCodeDependencyMapper,
)
from depanalyzer.parsers.registry import EcosystemRegistry
from depanalyzer.runtime.code_parser_pool import CodeParserPool
from depanalyzer.runtime.context import (
    AnalyzeContext,
    DetectContext,
    ExportContext,
    JoinContext,
    ParseContext,
    ResolveDepsContext,
    TransactionContext,
)
from depanalyzer.runtime.coordinator import TransactionCoordinator, TransactionResult
from depanalyzer.runtime.default_strategies import (
    DefaultAssetProjectionStrategy,
    DefaultCodeDependencyMapper,
)
from depanalyzer.runtime.dependency_collector import DependencyCollector
from depanalyzer.runtime.dependency_resolver import resolve_dependencies
from depanalyzer.runtime.eventbus import EventBus
from depanalyzer.runtime.graph_config import GraphBuildConfig
from depanalyzer.runtime.lifecycle import LifecyclePhase
from depanalyzer.runtime.strategies import (
    AnalyzeStrategy,
    AssetProjectionStrategy,
    CodeDependencyContext,
    CodeDependencyMapper,
    JoinStrategy,
    LifecycleHook,
    ProjectionContext,
)
from depanalyzer.runtime.worker import Task, TaskPriority, Worker
from depanalyzer.runtime.workspace import Workspace

if TYPE_CHECKING:
    from depanalyzer.runtime.progress import ProgressManager

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
        progress_manager: Optional["ProgressManager"] = None,
        enable_dependency_resolution: bool = False,
        max_dependencies: Optional[int] = None,
        graph_cache_root: Optional[Path] = None,
        dep_cache_root: Optional[Path] = None,
        workspace_cache_root: Optional[Path] = None,
        graph_build_config: Optional[GraphBuildConfig] = None,
        lifecycle_hooks: Optional[Sequence[LifecycleHook]] = None,
        code_dependency_mappers: Optional[Mapping[str, CodeDependencyMapper]] = None,
        asset_projection_strategy: Optional[AssetProjectionStrategy] = None,
        join_strategies: Optional[Sequence[JoinStrategy]] = None,
        analyze_strategies: Optional[Sequence[AnalyzeStrategy]] = None,
    ) -> None:
        """Initialize transaction.

        Args:
            source: Local path or Git URL.
            graph_id: Optional graph identifier. If None, generated from source.
            max_workers: Maximum concurrent workers.
            max_dependency_depth: Maximum third-party dependency depth.
            parent_transaction_id: Optional parent transaction ID for third-party deps.
            transaction_id: Optional transaction identifier. If None, generated.
            progress_manager: Optional progress manager for live progress display.
            enable_dependency_resolution: Whether to resolve third-party dependencies.
            max_dependencies: Optional global limit for third-party dependencies.
            graph_cache_root: Optional directory where graph cache files are stored.
                If None, defaults to .depanalyzer_cache/graphs in the current working directory.
            dep_cache_root: Optional directory where third-party dependency sources
                are cached. If None, defaults to .depanalyzer_cache/deps.
            workspace_cache_root: Optional directory where Git workspaces are cached.
                If None, defaults to .depanalyzer_cache/workspaces.
            graph_build_config: Optional GraphBuildConfig controlling how the
                dependency construct graph is built across all lifecycle phases.
            lifecycle_hooks: Optional lifecycle hooks invoked before/after phases.
            code_dependency_mappers: Optional mapping from ecosystem name to
                CodeDependencyMapper. When omitted, a default mapper is used.
            asset_projection_strategy: Optional AssetProjectionStrategy to use
                during ANALYZE. Defaults to an adapter over GraphManager.
            join_strategies: Optional additional JOIN phase strategies.
            analyze_strategies: Optional additional ANALYZE phase strategies.
        """
        self.transaction_id = transaction_id or str(uuid.uuid4())
        self.source = source
        self.graph_id = graph_id
        self.max_workers = max_workers
        self.max_dependency_depth = max_dependency_depth
        self.parent_transaction_id = parent_transaction_id
        self.enable_dependency_resolution = enable_dependency_resolution
        self.max_dependencies = max_dependencies

        # Graph build configuration (lifecycle‑wide).
        self._graph_build_config: GraphBuildConfig = (
            graph_build_config or GraphBuildConfig.default()
        )

        # Cache roots (can be overridden by CLI work-dir).
        self.graph_cache_root = Path(graph_cache_root) if graph_cache_root else None
        self.dep_cache_root = Path(dep_cache_root) if dep_cache_root else None
        self.workspace_cache_root = (
            Path(workspace_cache_root) if workspace_cache_root else None
        )

        # Core components (will be initialized in __setstate__ for deserialization).
        self.workspace = Workspace(source, cache_root=self.workspace_cache_root)
        self.worker: Optional[Worker] = None
        self.eventbus: Optional[EventBus] = None

        # Progress tracking.
        self._progress_manager = progress_manager

        # Strategies and hooks.
        self._lifecycle_hooks: List[LifecycleHook] = list(lifecycle_hooks or [])
        self._code_dependency_mappers: Dict[str, CodeDependencyMapper] = dict()
        if code_dependency_mappers is not None:
            self._code_dependency_mappers.update(code_dependency_mappers)
        else:
            # Install built-in per-ecosystem mappers by default so that
            # behavior matches the pre-strategy implementation.
            self._code_dependency_mappers.setdefault("cpp", CppCodeDependencyMapper())
            self._code_dependency_mappers.setdefault(
                "hvigor", HvigorCodeDependencyMapper()
            )

        self._default_code_dependency_mapper: CodeDependencyMapper = (
            DefaultCodeDependencyMapper()
        )
        self._asset_projection_strategy: AssetProjectionStrategy = (
            asset_projection_strategy or DefaultAssetProjectionStrategy()
        )
        self._join_strategies: List[JoinStrategy] = list(join_strategies or [])
        self._analyze_strategies: List[AnalyzeStrategy] = list(analyze_strategies or [])
        self._detected_targets: Dict[str, List[Path]] = {}

        # Will be initialized during run.
        self._graph_manager = None
        self._dependency_collector = None
        self._current_phase: Optional[LifecyclePhase] = None
        self._start_time = 0.0

        logger.info(
            "Transaction %s initialized for source: %s", self.transaction_id, source
        )
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
        state["_dependency_collector"] = None
        state["_progress_manager"] = None  # Progress manager is not serializable
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

            # Initialize DependencyCollector hook after eventbus is created
            if self._dependency_collector is None:
                self._dependency_collector = DependencyCollector(self.eventbus)
                logger.debug("DependencyCollector initialized")

        return self.eventbus

    # ------------------------------------------------------------------
    # Context construction and lifecycle hooks
    # ------------------------------------------------------------------

    def _make_base_context(self) -> TransactionContext:
        """Build a TransactionContext snapshot for the current state.

        Returns:
            TransactionContext: Snapshot of transaction-wide state.
        """
        return TransactionContext(
            transaction_id=self.transaction_id,
            graph_id=self.graph_id,
            source=self.source,
            workspace=self.workspace,
            graph=self._graph_manager,
            graph_build_config=self._graph_build_config,
            eventbus=self._ensure_eventbus(),
            contract_registry=ContractRegistry.get_instance(),
            progress_manager=self._progress_manager,
            enable_dependency_resolution=self.enable_dependency_resolution,
            max_dependency_depth=self.max_dependency_depth,
            max_dependencies=self.max_dependencies,
            current_phase=self._current_phase,
        )

    def _run_lifecycle_hooks_before(self, ctx: TransactionContext) -> None:
        """Run before-phase lifecycle hooks for the current phase.

        Args:
            ctx: Context snapshot for this phase.
        """
        for hook in self._lifecycle_hooks:
            if hook.phase == self._current_phase:
                try:
                    hook.before(ctx)
                except Exception:
                    logger.exception("Lifecycle hook %r before() failed", hook)

    def _run_lifecycle_hooks_after(self, ctx: TransactionContext) -> None:
        """Run after-phase lifecycle hooks for the current phase.

        Args:
            ctx: Context snapshot for this phase.
        """
        for hook in self._lifecycle_hooks:
            if hook.phase == self._current_phase:
                try:
                    hook.after(ctx)
                except Exception:
                    logger.exception("Lifecycle hook %r after() failed", hook)

    def _phase_acquire(self) -> None:
        """Phase A: Acquire source code."""
        logger.info("=== Phase: %s ===", LifecyclePhase.ACQUIRE)
        self._current_phase = LifecyclePhase.ACQUIRE

        # Hooks: before ACQUIRE.
        base_ctx = self._make_base_context()
        acquire_ctx = TransactionContext(**vars(base_ctx))
        self._run_lifecycle_hooks_before(acquire_ctx)

        # Start progress tracking for this phase
        phase_id = None
        if self._progress_manager:
            phase_id = self._progress_manager.start_phase(
                phase=LifecyclePhase.ACQUIRE,
                total=1,
                description="Acquiring source code",
            )

        root_path = self.workspace.acquire()
        logger.info("Workspace acquired at: %s", root_path)

        # Generate graph_id if not provided
        if not self.graph_id:
            self.graph_id = f"graph_{self.workspace.get_signature()}"
            logger.info("Generated graph_id: %s", self.graph_id)

        # Complete phase
        if self._progress_manager and phase_id:
            self._progress_manager.complete_phase(phase_id)

        # Hooks: after ACQUIRE.
        base_ctx = self._make_base_context()
        acquire_ctx = TransactionContext(**vars(base_ctx))
        self._run_lifecycle_hooks_after(acquire_ctx)

    def _phase_detect(self) -> None:
        """Phase B: Parallel detection of targets."""
        logger.info("=== Phase: %s ===", LifecyclePhase.DETECT)
        self._current_phase = LifecyclePhase.DETECT

        # Prepare context and run hooks.
        base_ctx = self._make_base_context()
        self._detected_targets: Dict[str, List[Path]] = {}
        detect_ctx = DetectContext(
            **vars(base_ctx),
            detected_targets=self._detected_targets,
        )
        self._run_lifecycle_hooks_before(detect_ctx)

        registry = EcosystemRegistry.get_instance()

        # Get all registered detector classes
        ecosystems = registry.list_ecosystems()
        logger.info("Registered ecosystems in registry: %s", ecosystems)
        if not ecosystems:
            logger.warning("No ecosystems registered, skipping detection")
            return

        logger.info("Running detectors for %d ecosystems", len(ecosystems))

        # Start progress tracking for this phase
        phase_id = None
        if self._progress_manager:
            phase_id = self._progress_manager.start_phase(
                phase=LifecyclePhase.DETECT,
                total=len(ecosystems),
                description=f"Detecting targets ({len(ecosystems)} ecosystems)",
            )

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
                    detect_cfg = self._graph_build_config.get_detect_config(eco)
                    detector = det_cls(
                        self.workspace.root_path,
                        eventbus,
                        config=detect_cfg,
                    )
                    targets = detector.detect()
                    logger.info("Detector %s found %d targets", eco, len(targets))
                    return {"ecosystem": eco, "targets": targets, "success": True}
                except Exception as e:
                    logger.error("Detector %s failed: %s", eco, e)
                    return {
                        "ecosystem": eco,
                        "targets": [],
                        "success": False,
                        "error": str(e),
                    }

            task = Task(
                task_id=f"detect_{ecosystem}",
                func=detect_task,
                priority=TaskPriority.HIGH,
            )
            worker.enqueue(task)

        # Log task count before execution
        task_count = worker.queued_task_count()
        logger.info("Enqueued %d detection tasks for execution", task_count)

        # Execute all detection tasks and collect results
        results = worker.run_all()
        logger.info("Detection tasks completed: %d results received", len(results))

        # Process results and store detected targets
        completed_count = 0
        for _task_id, result in results.items():
            if result.success and result.result:
                ecosystem = result.result.get("ecosystem")
                targets = result.result.get("targets", [])
                if ecosystem and targets:
                    self._detected_targets[ecosystem] = targets
                    logger.info(
                        "Stored %d targets for ecosystem: %s", len(targets), ecosystem
                    )

            completed_count += 1
            # Update progress after each ecosystem
            if self._progress_manager and phase_id:
                total_targets = sum(
                    len(targets) for targets in self._detected_targets.values()
                )
                self._progress_manager.update_phase(
                    phase_id,
                    completed=completed_count,
                    description=(
                        "Detected "
                        f"{total_targets} targets from "
                        f"{completed_count}/{len(ecosystems)} ecosystems"
                    ),
                )

        total_targets = sum(len(targets) for targets in self._detected_targets.values())
        logger.info(
            "Detection phase completed: %d ecosystems, %d total targets",
            len(self._detected_targets),
            total_targets,
        )

        # Complete phase
        if self._progress_manager and phase_id:
            self._progress_manager.complete_phase(phase_id)

        # Hooks: after DETECT.
        after_base_ctx = self._make_base_context()
        after_detect_ctx = DetectContext(
            **vars(after_base_ctx),
            detected_targets=self._detected_targets,
        )
        self._run_lifecycle_hooks_after(after_detect_ctx)

    def _phase_parse(self) -> None:
        """Phase C: Parallel parsing of detected targets.

        Two-stage parsing:
        1. Config parsing (CMakeLists.txt, hvigorfile.ts, etc.) via Worker thread pool
        2. Code parsing (source files) via CodeParserPool process pool
        """
        logger.info("=== Phase: %s ===", LifecyclePhase.PARSE)
        self._current_phase = LifecyclePhase.PARSE

        # Hooks: before PARSE (detected targets may have been populated).
        base_ctx = self._make_base_context()
        detected_targets: Dict[str, List[Path]] = getattr(self, "_detected_targets", {})
        parse_ctx = ParseContext(
            **vars(base_ctx),
            detected_targets=detected_targets,
            code_files_to_parse={},
        )
        self._run_lifecycle_hooks_before(parse_ctx)

        # Initialize GraphManager if not already done
        if self._graph_manager is None:
            # For third-party dependency transactions, use a namespace prefix
            # based on the workspace root directory name so that normalized
            # node IDs remain globally unique and clearly identify the
            # dependency repository.
            path_namespace = None
            if self.parent_transaction_id:
                try:
                    if self.workspace.root_path:
                        path_namespace = Path(self.workspace.root_path).name
                except Exception:
                    path_namespace = None

            self._graph_manager = GraphManager(
                graph_id=self.graph_id,
                root_path=self.workspace.root_path,
                path_namespace=path_namespace,
            )
            logger.info(
                "Initialized GraphManager with graph_id: %s, root_path: %s, namespace: %s",
                self.graph_id,
                self.workspace.root_path,
                path_namespace,
            )

        # Initialize dependency collector to collect DEPENDENCY_DISCOVERED events
        if self._dependency_collector is None:
            eventbus = self._ensure_eventbus()
            self._dependency_collector = DependencyCollector(eventbus)
            logger.info("Initialized DependencyCollector")

        if not hasattr(self, "_detected_targets") or not self._detected_targets:
            logger.info("No targets detected, skipping parse phase")
            # Hooks: after PARSE (no targets).
            after_base_ctx = self._make_base_context()
            after_ctx = ParseContext(
                **vars(after_base_ctx),
                detected_targets=getattr(self, "_detected_targets", {}),
                code_files_to_parse={},
            )
            self._run_lifecycle_hooks_after(after_ctx)
            return

        registry = EcosystemRegistry.get_instance()

        # Calculate total targets for progress tracking
        total_targets = sum(len(targets) for targets in self._detected_targets.values())

        # Start progress tracking for this phase
        phase_id = None
        if self._progress_manager:
            # Initial estimate: config files + potential code files
            estimated_total = total_targets * 10  # Rough estimate
            phase_id = self._progress_manager.start_phase(
                phase=LifecyclePhase.PARSE,
                total=estimated_total,
                description=f"Parsing {total_targets} config files",
            )

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

            logger.info(
                "Creating parse tasks for ecosystem %s: %d targets",
                ecosystem,
                len(targets),
            )

            for target_path in targets:

                def parse_task(eco=ecosystem, p_cls=parser_class, tgt=target_path):
                    """Config parsing task wrapper."""
                    try:
                        parser_cfg = self._graph_build_config.get_parser_config(eco)
                        parser = p_cls(
                            self.workspace.root_path,
                            self._graph_manager,
                            eventbus,
                            config=parser_cfg,
                        )
                        parser.parse(tgt)

                        # Discover code files from config parser
                        code_files = parser.discover_code_files()

                        logger.info(
                            "Parser %s processed %s, discovered %d code files",
                            eco,
                            tgt.name,
                            len(code_files),
                        )
                        return {
                            "ecosystem": eco,
                            "target": str(tgt),
                            "code_files": code_files,
                            "success": True,
                        }
                    except Exception as e:
                        logger.error("Parser %s failed for %s: %s", eco, tgt, e)
                        return {
                            "ecosystem": eco,
                            "target": str(tgt),
                            "code_files": [],
                            "success": False,
                            "error": str(e),
                        }

                # Use a stable, path-specific task_id to avoid deduplicating
                # different config files that share the same file name
                try:
                    rel_path = target_path.relative_to(self.workspace.root_path)
                    task_suffix = str(rel_path).replace("\\", "/")
                except ValueError:
                    task_suffix = str(target_path).replace("\\", "/")

                task = Task(
                    task_id=f"parse_{ecosystem}_{task_suffix}",
                    func=parse_task,
                    priority=TaskPriority.NORMAL,
                )
                worker.enqueue(task)

        # Execute all config parsing tasks
        results = worker.run_all()

        # Collect discovered code files and update progress
        config_parsed = 0
        for _task_id, result in results.items():
            config_parsed += 1
            if result.success and result.result:
                ecosystem = result.result.get("ecosystem")
                code_files = result.result.get("code_files", [])
                if ecosystem and code_files:
                    if ecosystem not in code_files_to_parse:
                        code_files_to_parse[ecosystem] = []
                    code_files_to_parse[ecosystem].extend(code_files)

            # Update progress after each config file
            if self._progress_manager and phase_id:
                self._progress_manager.update_phase(
                    phase_id,
                    completed=config_parsed,
                    description=f"Parsed {config_parsed}/{total_targets} config files",
                )

        total_config_success = sum(1 for r in results.values() if r.success)
        total_code_files = sum(len(files) for files in code_files_to_parse.values())
        logger.info(
            "Stage 1 completed: %d/%d config files parsed, %d code files discovered",
            total_config_success,
            len(results),
            total_code_files,
        )

        # Stage 2: Code parsing
        if not code_files_to_parse:
            logger.info("No code files to parse, skipping Stage 2")
            if self._progress_manager and phase_id:
                self._progress_manager.complete_phase(phase_id)
            return

        logger.info("=== Stage 2: Code Parsing ===")

        # Update progress total with actual code file count
        if self._progress_manager and phase_id:
            self._progress_manager.update_phase(
                phase_id,
                completed=config_parsed,
                description=f"Parsing {total_code_files} code files (Stage 2/2)",
            )
            # Reset total to reflect actual count
            phase_id_tmp = phase_id
            phase_id = self._progress_manager.start_phase(
                phase=LifecyclePhase.PARSE,
                total=total_code_files,
                description=f"Parsing {total_code_files} code files",
            )
            self._progress_manager.complete_phase(phase_id_tmp)

        code_pool = CodeParserPool.get_instance()

        # Submit all code files to process pool
        futures_list = []
        for ecosystem, file_paths in code_files_to_parse.items():
            logger.info(
                "Submitting %d code files for ecosystem: %s",
                len(file_paths),
                ecosystem,
            )
            code_cfg = self._graph_build_config.get_code_parser_config(ecosystem)
            batch_futures = code_pool.submit_batch(
                file_paths, ecosystem, config=code_cfg
            )
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
            elif parse_result.get("error"):
                failed += 1
                logger.warning(
                    "Code parsing failed for %s: %s",
                    file_path,
                    parse_result.get("error"),
                )
            else:
                # Add code file node and dependencies to graph
                self._process_code_parse_result(file_path, parse_result)
                successful += 1

            # Update progress after each code file
            total_processed = successful + skipped + failed
            if self._progress_manager and phase_id:
                self._progress_manager.update_phase(
                    phase_id,
                    completed=total_processed,
                    description=(
                        "Parsed "
                        f"{total_processed}/{total_code_files} code files "
                        f"({successful} ok, {failed} failed)"
                    ),
                )
                # Update graph metrics
                if self._graph_manager:
                    self._progress_manager.update_metrics(
                        node_count=self._graph_manager.node_count(),
                        edge_count=self._graph_manager.edge_count(),
                    )

        logger.info(
            "Stage 2 completed: %d successful, %d skipped, %d failed",
            successful,
            skipped,
            failed,
        )
        logger.info(
            "Parse phase completed: graph has %d nodes, %d edges",
            self._graph_manager.node_count(),
            self._graph_manager.edge_count(),
        )

        # Hooks: after PARSE.
        after_base_ctx = self._make_base_context()
        after_ctx = ParseContext(
            **vars(after_base_ctx),
            detected_targets=self._detected_targets,
            code_files_to_parse=code_files_to_parse,
        )
        self._run_lifecycle_hooks_after(after_ctx)

    def _process_code_parse_result(
        self, file_path: Path, parse_result: Dict[str, Any]
    ) -> None:
        """Process code parse result and update graph.

        The default implementation forwards to the configured
        CodeDependencyMapper. This preserves the public method as a
        stable entry point while making the mapping logic pluggable.

        Args:
            file_path: Path to parsed code file.
            parse_result: Parse result dictionary with includes/imports.
        """
        if not self._graph_manager:
            return

        tx_ctx = self._make_base_context()
        code_ctx = CodeDependencyContext(
            transaction_ctx=tx_ctx,
            file_path=file_path,
            parse_result=parse_result,
        )

        ecosystem = parse_result.get("ecosystem")
        mapper: CodeDependencyMapper
        if ecosystem and ecosystem in self._code_dependency_mappers:
            mapper = self._code_dependency_mappers[ecosystem]
        else:
            mapper = self._default_code_dependency_mapper

        try:
            mapper.map(code_ctx)
        except Exception:
            logger.exception("CodeDependencyMapper failed for file %s", file_path)

    def _phase_resolve_deps(self) -> None:
        """Phase D: Unified dependency resolution.

        Discovers dependencies, fetches them, and spawns child transactions in parallel.
        """
        logger.info("=== Phase: %s ===", LifecyclePhase.RESOLVE_DEPS)
        self._current_phase = LifecyclePhase.RESOLVE_DEPS

        # Hooks: before RESOLVE_DEPS.
        base_ctx = self._make_base_context()
        before_ctx = ResolveDepsContext(
            **vars(base_ctx),
            resolved_deps=[],
            dependency_specs=[],
        )
        self._run_lifecycle_hooks_before(before_ctx)

        if not self.enable_dependency_resolution:
            logger.info(
                "Dependency resolution is disabled for this transaction, skipping phase"
            )
            # Hooks: after RESOLVE_DEPS with no work performed.
            after_base_ctx = self._make_base_context()
            after_ctx = ResolveDepsContext(
                **vars(after_base_ctx),
                resolved_deps=[],
                dependency_specs=[],
            )
            self._run_lifecycle_hooks_after(after_ctx)
            return

        # Check if we've reached max depth
        if self.max_dependency_depth <= 0:
            logger.info("Max dependency depth reached, skipping dependency resolution")
            after_base_ctx = self._make_base_context()
            after_ctx = ResolveDepsContext(
                **vars(after_base_ctx),
                resolved_deps=[],
                dependency_specs=[],
            )
            self._run_lifecycle_hooks_after(after_ctx)
            return

        # Discover dependencies (returns List[DependencySpec])
        dep_specs = self._discover_dependencies()

        if not dep_specs:
            logger.info("No dependencies found")
            # Dependency resolution is enabled for this transaction but no
            # dependencies are currently present. To ensure that the global
            # package-level DAG does not retain stale edges, clear any
            # existing outgoing edges for this graph.
            if self.graph_id:
                try:
                    dag = GlobalDAG.get_instance(cache_dir=self.graph_cache_root)
                    dag.replace_dependencies(self.graph_id, set())
                    logger.info(
                        "Cleared GlobalDAG dependencies for %s "
                        "(no dependencies discovered)",
                        self.graph_id,
                    )
                except Exception as e:
                    logger.error(
                        "Failed to clear GlobalDAG dependencies for %s: %s",
                        self.graph_id,
                        e,
                        exc_info=True,
                    )
            after_base_ctx = self._make_base_context()
            after_ctx = ResolveDepsContext(
                **vars(after_base_ctx),
                resolved_deps=[],
                dependency_specs=dep_specs,
            )
            self._run_lifecycle_hooks_after(after_ctx)
            return

        logger.info("Found %d dependencies to resolve", len(dep_specs))

        if self.max_dependencies is not None and len(dep_specs) > self.max_dependencies:
            logger.info(
                "Applying max dependency limit: %d of %d",
                self.max_dependencies,
                len(dep_specs),
            )
            dep_specs = dep_specs[: self.max_dependencies]

        # Start progress tracking for this phase
        phase_id = None
        if self._progress_manager:
            phase_id = self._progress_manager.start_phase(
                phase=LifecyclePhase.RESOLVE_DEPS,
                total=len(dep_specs),
                description=f"Resolving {len(dep_specs)} dependencies",
            )

        # Resolve dependencies (fetch to local paths)
        resolved_deps = resolve_dependencies(
            dep_specs, cache_root=self.dep_cache_root or Path(".depanalyzer_cache/deps")
        )

        # Filter out failed dependencies
        successful_deps = [dep for dep in resolved_deps if dep["success"]]

        # Avoid creating child transactions for self-dependencies where the
        # resolved source path is the same as the current workspace root. This
        # can happen when semver ranges inside a package resolve to the same
        # package version and repository, which would otherwise create a
        # spurious self-loop in the GlobalDAG.
        try:
            workspace_root = Path(self.workspace.root_path).resolve()
        except Exception:
            workspace_root = None

        if workspace_root is not None:
            filtered_successful: List[Dict[str, Any]] = []
            for dep in successful_deps:
                source = dep.get("source")
                if not source:
                    filtered_successful.append(dep)
                    continue

                try:
                    dep_root = Path(source).resolve()
                except Exception:
                    filtered_successful.append(dep)
                    continue

                if dep_root == workspace_root:
                    logger.info(
                        "Skipping self-dependency %s for graph %s (source=%s)",
                        dep.get("name"),
                        self.graph_id,
                        source,
                    )
                    continue

                filtered_successful.append(dep)

            successful_deps = filtered_successful

        if not successful_deps:
            logger.warning("No dependencies were successfully fetched")
            if self._progress_manager and phase_id:
                self._progress_manager.complete_phase(phase_id)
            return

        logger.info(
            "Successfully fetched %d/%d dependencies",
            len(successful_deps),
            len(resolved_deps),
        )

        # Get global coordinator instance
        coordinator = TransactionCoordinator.get_instance()

        # Submit child transactions to process pool
        futures = []
        for dep in successful_deps:
            logger.info(
                "Creating child transaction for dependency: %s (parent: %s)",
                dep["name"],
                self.transaction_id,
            )

            child_tx = Transaction(
                source=dep["source"],
                graph_id=None,  # Will be generated in child
                max_workers=self.max_workers,
                max_dependency_depth=self.max_dependency_depth - 1,
                parent_transaction_id=self.transaction_id,
                enable_dependency_resolution=self.enable_dependency_resolution,
                max_dependencies=self.max_dependencies,
                graph_cache_root=self.graph_cache_root,
                dep_cache_root=self.dep_cache_root,
                workspace_cache_root=self.workspace_cache_root,
                graph_build_config=self._graph_build_config,
            )

            future = coordinator.submit(child_tx)
            futures.append((dep, future, child_tx.transaction_id))

        # Wait for all child transactions to complete
        logger.info("Waiting for %d child transactions to complete", len(futures))

        completed_count = 0
        failed_count = 0
        # Track child graph IDs that were successfully analyzed so that we can
        # update the package-level GlobalDAG once at the end of the phase.
        child_graph_ids: List[str] = []

        for dep, future, child_tx_id in futures:
            try:
                result = future.result(timeout=600)  # 10 minute timeout per child

                if result.success:
                    completed_count += 1
                    logger.info(
                        "Child transaction %s completed: %s (%d nodes, %d edges)",
                        child_tx_id,
                        dep["name"],
                        result.node_count,
                        result.edge_count,
                    )

                    # Link child graph into the current graph and remember its
                    # graph_id for GlobalDAG update after all children finish.
                    self._link_dependency_graph(result.graph_id, dep)
                    if result.graph_id:
                        child_graph_ids.append(result.graph_id)
                else:
                    failed_count += 1
                    logger.error(
                        "Child transaction %s failed: %s - %s",
                        child_tx_id,
                        dep["name"],
                        result.error,
                    )

            except Exception as e:
                failed_count += 1
                logger.error(
                    "Failed to get result for child transaction %s: %s", child_tx_id, e
                )

            # Update progress after each dependency
            if self._progress_manager and phase_id:
                total_processed = completed_count + failed_count
                self._progress_manager.update_phase(
                    phase_id,
                    completed=total_processed,
                    description=(
                        "Resolved "
                        f"{total_processed}/{len(futures)} dependencies "
                        f"({completed_count} ok, {failed_count} failed)"
                    ),
                )

        logger.info(
            "Dependency resolution completed: %d succeeded, %d failed",
            completed_count,
            failed_count,
        )

        # After all child transactions have completed and the parent graph has
        # been linked with proxy nodes, update the package-level GlobalDAG so
        # that it reflects the *current* dependency set for this graph.
        if self.graph_id and child_graph_ids:
            try:
                dag = GlobalDAG.get_instance(cache_dir=self.graph_cache_root)
                dag.replace_dependencies(self.graph_id, set(child_graph_ids))
                logger.info(
                    "Updated GlobalDAG dependencies for %s: %d children",
                    self.graph_id,
                    len(child_graph_ids),
                )
            except Exception as e:
                logger.error(
                    "Failed to update GlobalDAG dependencies for %s: %s",
                    self.graph_id,
                    e,
                    exc_info=True,
                )

        # Complete phase
        if self._progress_manager and phase_id:
            self._progress_manager.complete_phase(phase_id)

        # Hooks: after RESOLVE_DEPS with resolved dependencies.
        after_base_ctx = self._make_base_context()
        after_ctx = ResolveDepsContext(
            **vars(after_base_ctx),
            resolved_deps=resolved_deps,
            dependency_specs=dep_specs,
        )
        self._run_lifecycle_hooks_after(after_ctx)

        self._emit_progress_event(
            kind="phase_complete",
            phase=LifecyclePhase.RESOLVE_DEPS,
        )

    def _emit_progress_event(self, kind: str, phase: LifecyclePhase) -> None:
        """Emit a progress event.

        This is currently a lightweight compatibility hook used by the
        transaction to signal progress-related milestones. The initial
        implementation is intentionally minimal and only logs the event.

        Args:
            kind: Event kind (for example ``\"phase_complete\"``).
            phase: Lifecycle phase the event is associated with.

        Returns:
            None. The method is a no-op beyond logging.
        """
        try:
            logger.debug(
                "Progress event emitted: kind=%s, phase=%s",
                kind,
                phase,
            )
        except Exception:
            # Progress emission must never break transaction execution.
            logger.debug("Progress event emission failed", exc_info=True)

    def _discover_dependencies(self) -> List["DependencySpec"]:
        """Discover dependencies from the current graph.

        This method collects dependencies from two sources:
        1. DEPENDENCY_DISCOVERED events collected by DependencyCollector
        2. external_dep/external_library nodes in the GraphManager

        Returns:
            List[DependencySpec]: List of dependency specifications.
        """

        discovered = []

        # Method 1: Get dependencies from DependencyCollector (event-driven)
        if self._dependency_collector:
            event_deps = self._dependency_collector.get_discovered_dependencies()
            discovered.extend(event_deps)
            logger.info("Collected %d dependencies from events", len(event_deps))

        # Method 2: Scan GraphManager for external dependency nodes
        if self._graph_manager:
            graph_deps = []
            for node_id, node_attrs in self._graph_manager.nodes():
                node_type = node_attrs.get("type")

                # Check if node is an external dependency
                if node_type in ["external_dep", "external_library"]:
                    # Extract dependency information from node attributes
                    dep_spec = DependencySpec(
                        ecosystem=node_attrs.get("ecosystem", "unknown"),
                        name=node_attrs.get("name", node_id),
                        version=node_attrs.get("version"),
                        source_url=node_attrs.get("git_repository")
                        or node_attrs.get("source_url"),
                    )
                    graph_deps.append(dep_spec)

            discovered.extend(graph_deps)
            logger.info("Discovered %d dependencies from graph nodes", len(graph_deps))

        # Deduplicate dependencies by (ecosystem, name, version)
        unique_deps = {}
        for dep in discovered:
            key = (dep.ecosystem, dep.name, dep.version)
            if key not in unique_deps:
                unique_deps[key] = dep

        result = list(unique_deps.values())
        logger.info("Total unique dependencies discovered: %d", len(result))
        return result

    def _link_dependency_graph(
        self, child_graph_id: str, dep_info: Dict[str, Any]
    ) -> None:
        """Link a child dependency graph to the current graph.

        This method:
        1. Creates a Proxy node in the parent graph pointing to the child graph
        2. Connects matching external dependency nodes in the parent graph to
           the Proxy node so that cross-graph relationships are explicit.

        Args:
            child_graph_id: Graph ID of the child dependency.
            dep_info: Dependency information dict containing name, version, ecosystem, etc.
        """
        if not self._graph_manager:
            logger.warning("No graph manager, cannot link dependency graph")
            return

        dep_name = dep_info.get("name", "unknown")
        dep_version = dep_info.get("version", "latest")
        dep_ecosystem = dep_info.get("ecosystem", "unknown")

        # Attempt to detect license-only dependencies (e.g., OHPM packages without source)
        license_only = False
        source_root = dep_info.get("source")
        if source_root:
            try:
                meta_path = Path(source_root) / ".hvigor_meta.json"
                if meta_path.exists():
                    with meta_path.open("r", encoding="utf-8") as f:
                        meta = json.load(f)
                    license_only = bool(meta.get("license_only"))
            except Exception as meta_err:
                logger.debug(
                    "Failed to read Hvigor dependency metadata for %s from %s: %s",
                    dep_name,
                    source_root,
                    meta_err,
                )

        logger.info(
            "Linking dependency graph %s to current graph %s (dep: %s/%s)",
            child_graph_id,
            self.graph_id,
            dep_name,
            dep_version,
        )

        try:
            # 1. Create a Proxy node in parent graph pointing to child graph

            proxy_id = f"dep_graph:{child_graph_id}"

            self._graph_manager.add_node(
                proxy_id,
                NodeType.PROXY,
                child_graph_id=child_graph_id,
                source_path=dep_info.get("source"),
                ecosystem=dep_ecosystem,
                name=dep_name,
                version=dep_version,
                origin="external",
                provenance="third_party_dependency",
                kind="dependency_graph_proxy",
                license_only=license_only,
            )

            logger.debug(
                "Created Proxy node %s for child graph %s",
                proxy_id,
                child_graph_id,
            )
            # Find external library nodes in current graph that match this
            # dependency and link them to the proxy node. This provides a
            # cross-graph anchor in the parent graph, while the package-level
            # dependency ordering is managed separately via GlobalDAG.
            for node_id, node_attrs in self._graph_manager.nodes():
                node_type = node_attrs.get("type")

                if node_type in ["external_dep", "external_library"]:
                    # Check if this external node matches the dependency
                    node_name = node_attrs.get("name")
                    if node_name == dep_name:
                        # Create edge from external node to proxy
                        self._graph_manager.add_edge(
                            node_id,
                            proxy_id,
                            edge_kind=EdgeKind.DEPENDS_ON.value,
                            parser_name="transaction",
                            confidence=1.0,
                            link_class=LinkClass.CROSS_GRAPH.value,
                        )
                        logger.debug(
                            "Linked external node %s to proxy %s",
                            node_id,
                            proxy_id,
                        )

        except Exception as e:
            logger.error(
                "Failed to link dependency graph %s: %s",
                child_graph_id,
                e,
                exc_info=True,
            )

    def _phase_join(self) -> None:
        """Phase E: Join phase for hooks and cross-domain wiring."""
        logger.info("=== Phase: %s ===", LifecyclePhase.JOIN)
        self._current_phase = LifecyclePhase.JOIN

        if not self._graph_manager:
            logger.warning("GraphManager not initialized, skipping join phase")
            return

        # Hooks: before JOIN.
        base_ctx = self._make_base_context()
        join_ctx = JoinContext(**vars(base_ctx))
        self._run_lifecycle_hooks_before(join_ctx)

        # Event bus remains available for hooks that consume parse events,
        # but cross-ecosystem linking is now handled by per-ecosystem linkers.
        self._ensure_eventbus()

        # Run per-ecosystem linkers.
        registry = EcosystemRegistry.get_instance()
        ecosystems = registry.list_ecosystems()

        logger.info("Executing linkers for ecosystems: %s", ", ".join(ecosystems))

        for eco in ecosystems:
            linker_cls = registry.get_linker(eco)
            if not linker_cls:
                continue

            try:
                logger.info("Running linker for ecosystem: %s", eco)
                linker_cfg = self._graph_build_config.get_linker_config(eco)
                linker = linker_cls(self._graph_manager, config=linker_cfg)
                linker.link()
            except Exception as e:
                logger.error(
                    "Linker for ecosystem %s failed: %s", eco, e, exc_info=True
                )

        # Apply global, ecosystem-agnostic contract linking.
        try:
            logger.info("Running GlobalContractLinker for contract-based edges")
            GlobalContractLinker.link(
                self._graph_manager, config=self._graph_build_config.contract_match
            )
        except Exception as e:
            logger.error("GlobalContractLinker failed: %s", e, exc_info=True)

        # Run additional join strategies.
        after_base_ctx = self._make_base_context()
        final_join_ctx = JoinContext(**vars(after_base_ctx))
        for strategy in self._join_strategies:
            try:
                strategy.join(final_join_ctx)
            except Exception:
                logger.exception("JoinStrategy %r failed", strategy)

        # Hooks: after JOIN.
        self._run_lifecycle_hooks_after(final_join_ctx)

    def _phase_analyze(self) -> None:
        """Phase F: Analysis on transaction graph."""
        logger.info("=== Phase: %s ===", LifecyclePhase.ANALYZE)
        self._current_phase = LifecyclePhase.ANALYZE

        if not self._graph_manager:
            logger.warning("GraphManager not initialized, skipping analyze phase")
            return

        # Hooks: before ANALYZE.
        base_ctx = self._make_base_context()
        analyze_ctx = AnalyzeContext(**vars(base_ctx))
        self._run_lifecycle_hooks_before(analyze_ctx)

        # Start progress tracking for this phase (3 steps)
        phase_id = None
        if self._progress_manager:
            phase_id = self._progress_manager.start_phase(
                phase=LifecyclePhase.ANALYZE,
                total=3,
                description="Running analysis",
            )

        # 1. Derive asset->artifact projection
        logger.info("Deriving asset-to-artifact projection")
        try:
            if self._progress_manager and phase_id:
                self._progress_manager.add_active_task("Asset-artifact projection")

            projection_ctx = ProjectionContext(
                transaction_ctx=self._make_base_context(),
                graph=self._graph_manager,
                projection_config=self._graph_build_config.projection,
            )
            self._asset_projection_strategy.project(projection_ctx)
            logger.info("Asset-to-artifact projection completed")

            if self._progress_manager and phase_id:
                self._progress_manager.remove_active_task("Asset-artifact projection")
                self._progress_manager.update_phase(
                    phase_id,
                    completed=1,
                    description="Completed projection (1/3)",
                )
        except Exception as e:
            logger.error(
                "Failed to derive asset-artifact projection: %s", e, exc_info=True
            )
            if self._progress_manager and phase_id:
                self._progress_manager.remove_active_task("Asset-artifact projection")

        # 2. Deadcode analysis
        logger.info("Running deadcode analysis")
        try:
            if self._progress_manager and phase_id:
                self._progress_manager.add_active_task("Deadcode analysis")

            dc_analyzer = DeadcodeAnalyzer(self._graph_manager)
            dead_nodes = dc_analyzer.analyze()

            # Store results in graph metadata
            self._graph_manager.set_metadata("dead_nodes", list(dead_nodes))
            logger.info("Identified %d dead nodes", len(dead_nodes))

            if self._progress_manager and phase_id:
                self._progress_manager.remove_active_task("Deadcode analysis")
                self._progress_manager.update_phase(
                    phase_id,
                    completed=2,
                    description=f"Identified {len(dead_nodes)} dead nodes (2/3)",
                )
        except Exception as e:
            logger.error("Deadcode analysis failed: %s", e, exc_info=True)
            if self._progress_manager and phase_id:
                self._progress_manager.remove_active_task("Deadcode analysis")

        # 3. Linkage type analysis
        logger.info("Running linkage analysis")
        try:
            if self._progress_manager and phase_id:
                self._progress_manager.add_active_task("Linkage analysis")

            linkage_analyzer = LinkageAnalyzer(self._graph_manager)
            linkage_map = linkage_analyzer.analyze()

            # Store results in graph metadata
            self._graph_manager.set_metadata("linkage_map", linkage_map)
            logger.info("Analyzed %d linkage edges", len(linkage_map))

            if self._progress_manager and phase_id:
                self._progress_manager.remove_active_task("Linkage analysis")
                self._progress_manager.update_phase(
                    phase_id,
                    completed=3,
                    description=f"Analyzed {len(linkage_map)} linkage edges (3/3)",
                )
        except Exception as e:
            logger.error("Linkage analysis failed: %s", e, exc_info=True)
            if self._progress_manager and phase_id:
                self._progress_manager.remove_active_task("Linkage analysis")

        # Optional: uncertainty analysis (over-approximation labeling)
        try:
            policy: UncertaintyPolicyConfig = self._graph_build_config.uncertainty
            if policy.enabled:
                logger.info("Running uncertainty analysis")
                analyzer = UncertaintyAnalyzer(self._graph_manager, policy)
                analyzer.analyze()
        except Exception as e:
            logger.error("Uncertainty analysis failed: %s", e, exc_info=True)

        logger.info("Analysis phase completed")

        # Run additional analyze strategies.
        analyze_ctx_after = AnalyzeContext(**vars(self._make_base_context()))
        for strategy in self._analyze_strategies:
            try:
                strategy.analyze(analyze_ctx_after)
            except Exception:
                logger.exception("AnalyzeStrategy %r failed", strategy)

        # Complete phase
        if self._progress_manager and phase_id:
            self._progress_manager.complete_phase(phase_id)

        # Hooks: after ANALYZE.
        self._run_lifecycle_hooks_after(analyze_ctx_after)

    def _phase_export(self, output_path: Optional[Path] = None) -> None:
        """Phase G: Export results.

        Args:
            output_path: Optional output path for results.
        """
        logger.info("=== Phase: %s ===", LifecyclePhase.EXPORT)
        self._current_phase = LifecyclePhase.EXPORT

        if not self._graph_manager:
            logger.warning("No graph manager to export")
            return

        # Hooks: before EXPORT.
        base_ctx = self._make_base_context()
        export_ctx = ExportContext(
            **vars(base_ctx),
            output_path=output_path,
        )
        self._run_lifecycle_hooks_before(export_ctx)

        # Start progress tracking for this phase
        phase_id = None
        if self._progress_manager:
            phase_id = self._progress_manager.start_phase(
                phase=LifecyclePhase.EXPORT,
                total=1,
                description="Exporting results",
            )

        # Export to user-specified path if provided
        if output_path:
            try:
                if self._progress_manager and phase_id:
                    self._progress_manager.add_active_task("Exporting graph")

                # Ensure parent directory exists
                output_path = Path(output_path)
                output_path.parent.mkdir(parents=True, exist_ok=True)

                # Get the native NetworkX graph
                native_graph = self._graph_manager.backend.native_graph

                # Convert to node-link format
                graph_data = node_link_data(native_graph, edges="links")

                # Add metadata
                graph_metadata = {
                    "graph_id": self.graph_id,
                    "transaction_id": self.transaction_id,
                    "source": self.source,
                    "node_count": self._graph_manager.node_count(),
                    "edge_count": self._graph_manager.edge_count(),
                    "timestamp": time.time(),
                }

                # Combine metadata and graph data
                output_data = {
                    "metadata": graph_metadata,
                    "graph": graph_data,
                }

                # Write to file
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(output_data, f, indent=2)

                logger.info(
                    "Graph exported to: %s (%d nodes, %d edges)",
                    output_path,
                    self._graph_manager.node_count(),
                    self._graph_manager.edge_count(),
                )

                if self._progress_manager and phase_id:
                    self._progress_manager.remove_active_task("Exporting graph")

            except Exception as e:
                logger.error("Failed to export graph to %s: %s", output_path, e)
                if self._progress_manager and phase_id:
                    self._progress_manager.remove_active_task("Exporting graph")

        logger.info("Export phase completed")

        # Complete phase
        if self._progress_manager and phase_id:
            self._progress_manager.complete_phase(phase_id)

        # Hooks: after EXPORT.
        self._run_lifecycle_hooks_after(export_ctx)

    def _flush_graph_to_disk(self) -> Optional[Path]:
        """Serialize graph to disk and return cache path.

        This method serializes the GraphManager to a JSON file using NetworkX's
        node_link_data format. The graph is saved to a cache directory for later
        retrieval.

        Returns:
            Optional[Path]: Path to the serialized graph file, or None if no graph.
        """
        if not self._graph_manager:
            logger.warning("No graph manager to serialize")
            return None

        if self._graph_manager.node_count() == 0:
            logger.info("Graph is empty, skipping serialization")
            return None

        try:
            # Create cache directory
            cache_dir = self.graph_cache_root or (Path(".depanalyzer_cache") / "graphs")
            cache_dir.mkdir(parents=True, exist_ok=True)

            # Generate cache file path
            cache_file = cache_dir / f"{self.graph_id}.json"

            # Build an export-only graph with canonical IDs and schemas
            native_graph = self._graph_manager.backend.native_graph
            export_graph = MultiDiGraph()

            node_id_map: Dict[str, str] = {}

            # Normalize nodes
            for raw_id, attrs in native_graph.nodes(data=True):
                canonical_id, canonical_attrs = canonicalize_node(
                    str(raw_id),
                    attrs or {},
                    self._graph_manager.root_path,
                )
                node_id_map[str(raw_id)] = canonical_id
                export_graph.add_node(canonical_id, **canonical_attrs)

            # Normalize edges
            for u, v, _key, attrs in native_graph.edges(data=True, keys=True):
                source_id = node_id_map.get(str(u), str(u))
                target_id = node_id_map.get(str(v), str(v))
                canonical_attrs = canonicalize_edge(attrs or {})
                export_graph.add_edge(source_id, target_id, **canonical_attrs)

            # Convert to node-link format
            graph_data = node_link_data(export_graph, edges="links")

            # Add metadata
            try:
                revision = self.workspace.get_revision()
            except Exception:
                revision = None

            graph_metadata = {
                "graph_id": self.graph_id,
                "transaction_id": self.transaction_id,
                "source": self.source,
                "node_count": self._graph_manager.node_count(),
                "edge_count": self._graph_manager.edge_count(),
                "timestamp": time.time(),
                # Persist root_path and path_namespace for downstream tools
                # (e.g. scancode integration and path de-normalization).
                "root_path": (
                    str(self._graph_manager.root_path)
                    if self._graph_manager.root_path
                    else None
                ),
                "path_namespace": getattr(self._graph_manager, "path_namespace", None),
                # Optional revision identifier (e.g. Git commit SHA) for Git-based
                # workspaces. This complements the path/URL-based graph_id so that
                # consumers can distinguish graphs for different revisions.
                "revision": revision,
            }

            # Combine metadata and graph data
            output_data = {
                "metadata": graph_metadata,
                "graph": graph_data,
            }

            # Write to file
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(output_data, f, indent=2)

            logger.info(
                "Graph serialized to: %s (%d nodes, %d edges)",
                cache_file,
                self._graph_manager.node_count(),
                self._graph_manager.edge_count(),
            )

            return cache_file

        except Exception as e:
            logger.error("Failed to serialize graph: %s", e, exc_info=True)
            return None

    def run(self, output_path: Optional[Path] = None) -> "TransactionResult":
        """Execute full transaction lifecycle.

        Args:
            output_path: Optional output path for export phase.

        Returns:
            TransactionResult: Transaction execution result.
        """
        registry = EcosystemRegistry.get_instance()
        ecosystems = registry.list_ecosystems()
        logger.info(
            "[PID %d] Available ecosystems in this process: %s", os.getpid(), ecosystems
        )

        self._start_time = time.time()
        logger.info(
            "[PID %d] Starting transaction %s for: %s",
            os.getpid(),
            self.transaction_id,
            self.source,
        )

        # Start transaction progress tracking
        if self._progress_manager:
            self._progress_manager.start_transaction(
                graph_id=self.graph_id or "unknown",
                source=self.source,
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

            # Return graph manager (will be properly initialized in later implementation)
            if not self._graph_manager:
                path_namespace = None
                if self.parent_transaction_id:
                    try:
                        if self.workspace.root_path:
                            path_namespace = Path(self.workspace.root_path).name
                    except Exception:
                        path_namespace = None

                self._graph_manager = GraphManager(
                    root_path=self.workspace.root_path,
                    path_namespace=path_namespace,
                )

            # Serialize graph to disk before building result
            cache_path = self._flush_graph_to_disk()
            if cache_path:
                logger.info("Graph cached at: %s", cache_path)

                # Register graph in GraphRegistry
                registry = GraphRegistry.get_instance(cache_root=self.graph_cache_root)
                summary = {
                    "node_count": self._graph_manager.node_count(),
                    "edge_count": self._graph_manager.edge_count(),
                    "source": self.source,
                    "transaction_id": self.transaction_id,
                }
                registry.register(
                    graph_id=self.graph_id,
                    cache_path=cache_path,
                    summary=summary,
                )
                logger.info("Registered graph %s in GraphRegistry", self.graph_id)

            # Update final metrics
            if self._progress_manager and self._graph_manager:
                self._progress_manager.update_metrics(
                    node_count=self._graph_manager.node_count(),
                    edge_count=self._graph_manager.edge_count(),
                )

            # Build result
            result = TransactionResult(
                transaction_id=self.transaction_id,
                graph_id=self.graph_id or "unknown",
                success=True,
                node_count=(
                    self._graph_manager.node_count() if self._graph_manager else 0
                ),
                edge_count=(
                    self._graph_manager.edge_count() if self._graph_manager else 0
                ),
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
