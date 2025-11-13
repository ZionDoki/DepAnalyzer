"""Export command implementation."""

import logging
from pathlib import Path

from depanalyzer.graph.manager import GraphManager

logger = logging.getLogger("depanalyzer.cli.export")


def export_command(args) -> int:
    """Execute export command.

    Args:
        args: Parsed command-line arguments.

    Returns:
        int: Exit code.
    """
    logger.info("=== Depanalyzer Export ===")
    logger.info("Input: %s", args.input)
    logger.info("Output: %s", args.output)

    try:
        # Load graph
        input_path = Path(args.input)
        input_format = input_path.suffix.lstrip(".")
        if input_format not in ["json", "gml"]:
            input_format = "json"

        logger.info("Loading graph from: %s", input_path)
        graph_manager = GraphManager.load(input_path, format=input_format)

        logger.info("Loaded graph: %d nodes, %d edges",
                    graph_manager.node_count(), graph_manager.edge_count())

        # Export graph
        output_path = Path(args.output)
        output_format = args.format or output_path.suffix.lstrip(".")
        if output_format not in ["json", "gml", "dot"]:
            output_format = "json"

        logger.info("Exporting to %s format", output_format)
        graph_manager.save(output_path, format=output_format)

        logger.info("Export completed: %s", output_path)
        return 0

    except Exception as e:
        logger.error("Export failed: %s", e, exc_info=True)
        return 1
