"""Scan command implementation."""

import json
import logging
import shutil
import time
from pathlib import Path

from depanalyzer.runtime.coordinator import TransactionCoordinator
from depanalyzer.runtime.progress import ProgressManager, LogBufferHandler
from depanalyzer.runtime.transaction import Transaction

logger = logging.getLogger("depanalyzer.cli.scan")


def scan_command(args) -> int:
    """Execute scan command.

    Args:
        args: Parsed command-line arguments.

    Returns:
        int: Exit code.
    """
    # Add a top-level exception handler to catch ANY unhandled exceptions
    import sys
    import traceback

    try:
        return _scan_command_impl(args)
    except Exception as e:
        # Print to stderr directly to ensure it's visible even if logging is broken
        print(f"\n{'=' * 70}", file=sys.stderr)
        print(f"FATAL ERROR in scan_command:", file=sys.stderr)
        print(f"{'=' * 70}", file=sys.stderr)
        print(f"Exception: {e}", file=sys.stderr)
        print(f"\nTraceback:", file=sys.stderr)
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
    logger.info("=== Depanalyzer Scan ===")
    logger.info("Source: %s", args.source)
    logger.info("Output: %s", args.output)
    logger.info("Workers: %d", args.workers)
    logger.info("Max depth: %d", args.max_depth)
    logger.info("Third-party resolution enabled: %s", getattr(args, "third_party", False))

    start_time = time.time()

    # Create progress manager
    progress_enabled = not getattr(args, 'no_progress', False)
    progress_manager = ProgressManager(
        enabled=progress_enabled,
        live_panel=True,
    )

    # Reconfigure logging to use LogBuffer for coordinated output
    # This prevents logger output from interfering with progress display
    if progress_enabled:
        root_logger = logging.getLogger()
        # Remove existing handlers
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

        # Add LogBufferHandler to capture logs in progress display
        buffer_handler = LogBufferHandler(progress_manager.log_buffer)
        buffer_handler.setFormatter(
            logging.Formatter("[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
        )
        buffer_handler.setLevel(logging.DEBUG)  # Ensure handler captures all logs
        root_logger.addHandler(buffer_handler)
        root_logger.setLevel(logging.DEBUG)  # Ensure root logger passes all logs

    try:
        # Create coordinator with process pool
        logger.info("Creating TransactionCoordinator...")
        coordinator = TransactionCoordinator.get_instance()
        logger.info("TransactionCoordinator created successfully")

        # Create and submit transaction to process pool
        logger.info("Creating transaction...")
        transaction = Transaction(
            source=args.source,
            max_workers=args.workers,
            max_dependency_depth=args.max_depth,
            enable_dependency_resolution=getattr(args, "third_party", False),
            max_dependencies=getattr(args, "max_deps", None),
            progress_manager=progress_manager,
        )
        logger.info("Transaction created: %s", transaction.transaction_id)

        # Use progress manager's live display context
        logger.info("Starting live display...")
        with progress_manager.live_display():
            logger.info("Submitting transaction %s to process pool", transaction.transaction_id)
            try:
                future = coordinator.submit(transaction)
                logger.info("Transaction submitted successfully")
            except Exception as e:
                logger.error("Failed to submit transaction: %s", e, exc_info=True)
                return 1

            # Wait for transaction to complete (with timeout)
            logger.info("Waiting for transaction to complete...")
            try:
                result = future.result(timeout=300)  # 5 minute timeout
                logger.info("Transaction completed, processing result...")
            except TimeoutError:
                logger.error("Transaction timed out after 300 seconds")
                logger.error("This usually indicates the subprocess is stuck or crashed")
                logger.error("Check the logs above for any error messages")
                return 1
            except Exception as e:
                logger.error("Transaction failed with exception: %s", e, exc_info=True)
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

        # Get graph from registry for export
        from depanalyzer.graph.registry import GraphRegistry

        registry = GraphRegistry()
        cache_path = registry.get_cache_path(result.graph_id)

        if cache_path:
            logger.info("Graph cached at: %s", cache_path)

            # Export graph to user-specified output path
            output_path = Path(args.output)
            try:
                # Create output directory if needed
                output_path.parent.mkdir(parents=True, exist_ok=True)

                # Copy cached graph to output location
                shutil.copy2(cache_path, output_path)
                logger.info("Graph exported to: %s", output_path)

                # Also log summary information
                try:
                    with open(cache_path, "r", encoding="utf-8") as f:
                        graph_data = json.load(f)
                        metadata = graph_data.get("metadata", {})
                        logger.info(
                            "Export summary: %d nodes, %d edges, source: %s",
                            metadata.get("node_count", 0),
                            metadata.get("edge_count", 0),
                            metadata.get("source", "unknown"),
                        )
                except Exception as e:
                    logger.debug("Could not read graph metadata: %s", e)

            except Exception as e:
                logger.error("Failed to export graph: %s", e)
                return 1
        else:
            logger.warning("Graph not found in registry: %s", result.graph_id)
            return 1

        # Shutdown coordinator
        coordinator.shutdown()

        return 0

    except Exception as e:
        logger.error("Scan failed: %s", e, exc_info=True)
        return 1
    finally:
        # Stop progress manager
        progress_manager.stop()

        # Ensure cleanup of coordinator
        try:
            TransactionCoordinator.get_instance().shutdown(wait=False)
        except:
            pass

        # Ensure cleanup of GraphRegistry manager
        try:
            from depanalyzer.graph.registry import GraphRegistry
            GraphRegistry.shutdown()
        except:
            pass
