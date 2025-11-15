"""Scan command implementation."""

import json
import logging
import shutil
import time
from pathlib import Path

from depanalyzer.runtime.coordinator import TransactionCoordinator
from depanalyzer.runtime.progress import ProgressManager
from depanalyzer.runtime.transaction import Transaction

logger = logging.getLogger("depanalyzer.cli.scan")


def scan_command(args) -> int:
    """Execute scan command.

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

    start_time = time.time()

    # Create progress manager
    progress_enabled = not getattr(args, 'no_progress', False)
    progress_manager = ProgressManager(
        enabled=progress_enabled,
        live_panel=True,
    )

    try:
        # Create coordinator with process pool
        coordinator = TransactionCoordinator.get_instance()

        # Create and submit transaction to process pool
        transaction = Transaction(
            source=args.source,
            max_workers=args.workers,
            max_dependency_depth=args.max_depth,
            progress_manager=progress_manager,
        )

        # Use progress manager's live display context
        with progress_manager.live_display():
            logger.info("Submitting transaction %s to process pool", transaction.transaction_id)
            future = coordinator.submit(transaction)

            # Wait for transaction to complete
            logger.info("Waiting for transaction to complete...")
            result = future.result()

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

        # Ensure cleanup
        try:
            TransactionCoordinator.get_instance().shutdown(wait=False)
        except:
            pass
