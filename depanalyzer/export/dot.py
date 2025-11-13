"""DOT export for transaction graphs."""

import logging
from pathlib import Path

import networkx as nx

from depanalyzer.graph.manager import GraphManager

logger = logging.getLogger("depanalyzer.export.dot")


def export_dot(graph_manager: GraphManager, output_path: Path) -> None:
    """Export graph to DOT format.

    Args:
        graph_manager: Graph manager to export.
        output_path: Output file path.
    """
    logger.info("Exporting graph to DOT: %s", output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Use pydot if available, otherwise write_dot
    try:
        from networkx.drawing.nx_pydot import write_dot
        write_dot(graph_manager._backend.native_graph, str(output_path))
    except ImportError:
        # Fallback to agraph if pydot not available
        try:
            from networkx.drawing.nx_agraph import write_dot
            write_dot(graph_manager._backend.native_graph, str(output_path))
        except ImportError:
            logger.warning("Neither pydot nor pygraphviz available, DOT export skipped")
            return

    logger.info("DOT export completed: %d nodes, %d edges",
                graph_manager.node_count(), graph_manager.edge_count())
