"""Scan command implementation."""

import logging
import time
from pathlib import Path

from runtime.transaction import Transaction

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
        # Create and run transaction
        transaction = Transaction(
            source=args.source,
            max_workers=args.workers,
            max_dependency_depth=args.max_depth,
        )

        output_path = Path(args.output)
        graph_manager = transaction.run(output_path=output_path)

        # Save graph
        format_ext = output_path.suffix.lstrip(".")
        if format_ext not in ["json", "gml", "dot"]:
            format_ext = "json"

        graph_manager.save(output_path, format=format_ext)

        elapsed = time.time() - start_time
        logger.info("Scan completed in %.2fs", elapsed)
        logger.info("Graph: %d nodes, %d edges",
                    graph_manager.node_count(), graph_manager.edge_count())
        logger.info("Output: %s", output_path)

        return 0

    except Exception as e:
        logger.error("Scan failed: %s", e, exc_info=True)
        return 1
