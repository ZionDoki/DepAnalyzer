"""Scan command implementation."""

# CLI must gracefully handle unexpected failures to present user-friendly errors.


import json
import logging
import shutil
import sys
import time
import traceback
from concurrent.futures import CancelledError
from pathlib import Path
from pickle import PickleError
from typing import Optional

from depanalyzer.graph import GlobalDAG, GraphRegistry, merge_graph_with_dependencies
from depanalyzer.runtime.config_loader import load_graph_build_config
from depanalyzer.runtime.display import RichDisplayManager
from depanalyzer.runtime.task_pool import GlobalTaskPool, shutdown_pool
from depanalyzer.runtime.transaction import Transaction

logger = logging.getLogger("depanalyzer.cli.scan")

RECOVERABLE_SCAN_ERRORS = (
    AttributeError,
    json.JSONDecodeError,
    OSError,
    RuntimeError,
    shutil.Error,
    TimeoutError,
    TypeError,
    ValueError,
)


def scan_command(args) -> int:
    """Execute scan command.

    Args:
        args: Parsed command-line arguments.

    Returns:
        int: Exit code.
    """
    try:
        return _scan_command_impl(args)
    except RECOVERABLE_SCAN_ERRORS as e:
        # Print to stderr directly to ensure it's visible even if logging is broken
        print(f"\n{'=' * 70}", file=sys.stderr)
        print("FATAL ERROR in scan_command:", file=sys.stderr)
        print(f"{'=' * 70}", file=sys.stderr)
        print(f"Exception: {e}", file=sys.stderr)
        print("\nTraceback:", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        print(f"{'=' * 70}\n", file=sys.stderr)
        return 1


def _scan_command_impl(args) -> int:
    """Internal implementation of scan command.

    Args:
        args: Parsed command-line arguments.

    Returns:
        int: Exit code.
    """
    logger.debug("=== Depanalyzer Scan ===")
    logger.debug("Source: %s", args.source)
    output = getattr(args, "output", None)
    cache_dir_arg = getattr(args, "cache_dir", None)
    third_party_enabled = getattr(args, "third_party", False)
    wait_timeout = getattr(args, "timeout", 900)
    if output:
        logger.debug("Output: %s", output)
    if cache_dir_arg:
        logger.debug("Cache dir: %s", cache_dir_arg)
    logger.debug("Workers: %d", args.workers)
    logger.debug("Max depth: %d", args.max_depth)
    logger.debug("Scan timeout (s): %d", wait_timeout)
    logger.debug("Third-party resolution enabled: %s", third_party_enabled)

    start_time = time.time()

    sources_root: Optional[Path] = None
    graphs_root: Optional[Path] = None
    workspace_cache_root: Optional[Path] = None

    # Derive cache directory layout:
    # base_cache_dir / <source_stem> / {sources, graphs}
    try:
        base_cache_dir = (
            Path(cache_dir_arg).expanduser().resolve()
            if cache_dir_arg
            else Path(".dep_cache").resolve()
        )
    except (OSError, RuntimeError, ValueError) as e:
        logger.error("Failed to resolve cache-dir %s: %s", cache_dir_arg, e)
        return 1

    source_stem = Path(args.source).stem
    cache_root = base_cache_dir / source_stem

    sources_root = cache_root / "sources"
    graphs_root = cache_root / "graphs"
    workspace_cache_root = sources_root / "workspaces"

    try:
        sources_root.mkdir(parents=True, exist_ok=True)
        graphs_root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error(
            "Failed to initialize cache subdirectories under %s: %s", cache_root, e
        )
        return 1

    try:
        # Load graph-build configuration (TOML/JSON or inline string) if provided
        config_arg = getattr(args, "config", None)
        graph_build_config = load_graph_build_config(config_arg)
        if getattr(args, "fallback_tree", False):
            graph_build_config.fallback.enabled = True

        # GlobalTaskPool will be initialized lazily when first task is submitted
        # No need to pre-initialize here
        logger.debug("Workers: %d (GlobalTaskPool will initialize on first use)", args.workers)

        # Create display manager for progress tracking
        log_file = getattr(args, "log_file", None)
        display_manager = RichDisplayManager(
            enabled=True,
            log_file=log_file,
        )

        # Create transaction (runs in main process, tasks are parallelized)
        logger.debug("Creating transaction...")
        transaction = Transaction(
            source=args.source,
            max_workers=args.workers,
            max_dependency_depth=args.max_depth,
            enable_dependency_resolution=getattr(args, "third_party", False),
            skip_analysis=getattr(args, "no_analyze", False),
            max_dependencies=getattr(args, "max_deps", None),
            max_concurrent_deps=getattr(args, "concurrent_deps", 4),
            graph_cache_root=graphs_root,
            dep_cache_root=sources_root,
            workspace_cache_root=workspace_cache_root,
            graph_build_config=graph_build_config,
            display_manager=display_manager,
        )
        logger.debug("Transaction created: %s", transaction.transaction_id)

        # Run transaction directly (parallelism happens at task level via GlobalTaskPool)
        logger.debug("Running transaction %s...", transaction.transaction_id)
        try:
            with display_manager.live_display():
                result = transaction.run()
            logger.info("Transaction completed, processing result...")
        except KeyboardInterrupt:
            logger.warning("Transaction interrupted by user (Ctrl+C)")
            display_manager.stop()
            shutdown_pool(wait=False, force=True)
            return 130  # Standard exit code for SIGINT
        except (CancelledError, OSError, RuntimeError, ValueError) as e:
            logger.error("Transaction failed with exception: %s", e, exc_info=True)
            display_manager.stop()
            return 1

        if not result.success:
            logger.error("Transaction failed: %s", result.error)
            return 1

        elapsed = time.time() - start_time
        logger.info("Scan completed in %.2fs", elapsed)
        logger.info(
            "Graph: %d nodes, %d edges (graph_id: %s)",
            result.node_count,
            result.edge_count,
            result.graph_id,
        )

        registry = GraphRegistry.get_instance(cache_root=graphs_root)
        cache_path = registry.get_cache_path(result.graph_id)

        if cache_path:
            logger.info("Graph cached at: %s", cache_path)

            # If an explicit output path was provided, write graph there.
            # When third-party resolution is enabled (-t), export a merged
            # graph that includes all dependency graphs.
            if output:
                output_path = Path(output)
                try:
                    # Create output directory if needed
                    output_path.parent.mkdir(parents=True, exist_ok=True)

                    if third_party_enabled:
                        graphs_root_path = graphs_root or Path(
                            ".depanalyzer_cache/graphs"
                        )
                        merged_data = merge_graph_with_dependencies(
                            result.graph_id,
                            cache_root=graphs_root_path,
                        )

                        with output_path.open("w", encoding="utf-8") as f:
                            json.dump(merged_data, f, indent=2)

                        meta = merged_data.get("metadata", {})
                        logger.info(
                            "Merged graph exported to: %s (%d nodes, %d edges)",
                            output_path,
                            meta.get("node_count", 0),
                            meta.get("edge_count", 0),
                        )
                    else:
                        # Copy cached main graph to output location
                        shutil.copy2(cache_path, output_path)
                        logger.info("Graph exported to: %s", output_path)

                        # Also log summary information
                        try:
                            with cache_path.open("r", encoding="utf-8") as f:
                                graph_data = json.load(f)
                                metadata = graph_data.get("metadata", {})
                                logger.info(
                                    "Export summary: %d nodes, %d edges, source: %s",
                                    metadata.get("node_count", 0),
                                    metadata.get("edge_count", 0),
                                    metadata.get("source", "unknown"),
                                )
                        except (OSError, json.JSONDecodeError, ValueError) as e:
                            logger.debug("Could not read graph metadata: %s", e)

                except (OSError, json.JSONDecodeError, shutil.Error, ValueError) as e:
                    logger.error("Failed to export graph: %s", e)
                    return 1
            else:
                # In work-dir mode we rely on the cached main graph and, when
                # third-party resolution is enabled, also provide a merged
                # graph next to it.
                logger.info(
                    "Graph stored under work directory: %s",
                    cache_path,
                )

                if third_party_enabled:
                    try:
                        graphs_root_path = graphs_root or Path(
                            ".depanalyzer_cache/graphs"
                        )
                        merged_data = merge_graph_with_dependencies(
                            result.graph_id,
                            cache_root=graphs_root_path,
                        )

                        merged_path = cache_path.with_name(
                            f"{result.graph_id}_merged.json"
                        )
                        with merged_path.open("w", encoding="utf-8") as f:
                            json.dump(merged_data, f, indent=2)

                        meta = merged_data.get("metadata", {})
                        logger.info(
                            "Merged graph stored under work directory: %s (%d nodes, %d edges)",
                            merged_path,
                            meta.get("node_count", 0),
                            meta.get("edge_count", 0),
                        )
                    except (
                        OSError,
                        json.JSONDecodeError,
                        RuntimeError,
                        ValueError,
                    ) as e:
                        logger.error(
                            "Failed to create merged graph: %s", e, exc_info=True
                        )

            # Log high-level information about third-party dependency graphs so
            # users can see how many transactions were spawned.
            try:
                graphs_root_path = graphs_root or Path(".depanalyzer_cache/graphs")
                global_dag = GlobalDAG.get_instance(cache_dir=graphs_root_path)
                dep_graph_ids = global_dag.get_transitive_dependencies(result.graph_id)

                if dep_graph_ids:
                    sorted_ids = sorted(dep_graph_ids)
                    logger.info(
                        "Third-party dependency graphs: %d (%s)",
                        len(sorted_ids),
                        ", ".join(sorted_ids),
                    )

                    # Log a brief summary for each dependency graph so users can
                    # understand what was analyzed in child transactions.
                    for dep_id in sorted_ids:
                        dep_entry = registry.get_entry(dep_id)
                        if not dep_entry:
                            logger.info(
                                "  - %s: (no registry entry, graph may have failed)",
                                dep_id,
                            )
                            continue

                            # unreachable
                        summary = dep_entry.get("summary", {})
                        logger.info(
                            "  - %s: %d nodes, %d edges (source: %s)",
                            dep_id,
                            summary.get("node_count", 0),
                            summary.get("edge_count", 0),
                            summary.get("source", "unknown"),
                        )
                else:
                    # Only log an explicit 0 when third-party resolution was enabled;
                    # otherwise this is expected.
                    if third_party_enabled:
                        logger.info("Third-party dependency graphs: 0")
            except (OSError, RuntimeError, ValueError) as e:
                logger.debug(
                    "Could not summarize third-party dependency graphs for %s: %s",
                    result.graph_id,
                    e,
                )
        else:
            logger.warning("Graph not found in registry: %s", result.graph_id)
            return 1

        # Shutdown task pool - use wait=False on Windows to avoid hangs
        shutdown_pool(wait=False, force=True)

        return 0

    except RECOVERABLE_SCAN_ERRORS as e:
        logger.error("Scan failed: %s", e, exc_info=True)
        return 1
    finally:
        # Ensure cleanup of GlobalTaskPool
        try:
            shutdown_pool(wait=False, force=True)
        except (OSError, RuntimeError):
            pass

        # Ensure cleanup of GraphRegistry singleton state
        try:
            GraphRegistry.shutdown()
        except (OSError, RuntimeError):
            pass
