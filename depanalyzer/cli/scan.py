"""Scan command implementation."""

import logging
import time
from pathlib import Path

from depanalyzer.runtime.coordinator import TransactionCoordinator
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

    try:
        # Create coordinator with process pool
        coordinator = TransactionCoordinator.get_instance()

        # Create and submit transaction to process pool
        transaction = Transaction(
            source=args.source,
            max_workers=args.workers,
            max_dependency_depth=args.max_depth,
        )

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

            # TODO: Implement export from cache
            output_path = Path(args.output)
            logger.info("Output: %s", output_path)
            logger.info("Export functionality to be implemented")
        else:
            logger.warning("Graph not found in registry: %s", result.graph_id)

        # Shutdown coordinator
        coordinator.shutdown()

        return 0

    except Exception as e:
        logger.error("Scan failed: %s", e, exc_info=True)
        return 1
    finally:
        # Ensure cleanup
        try:
            TransactionCoordinator.get_instance().shutdown(wait=False)
        except:
            pass
