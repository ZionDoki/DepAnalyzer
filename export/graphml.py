"""GraphML export for transaction graphs."""

import logging
from pathlib import Path

import networkx as nx

from graph.manager import GraphManager

logger = logging.getLogger("depanalyzer.export.graphml")


def export_graphml(graph_manager: GraphManager, output_path: Path) -> None:
    """Export graph to GraphML format.

    Args:
        graph_manager: Graph manager to export.
        output_path: Output file path.
    """
    logger.info("Exporting graph to GraphML: %s", output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    nx.write_graphml(graph_manager._backend.native_graph, str(output_path))

    logger.info("GraphML export completed: %d nodes, %d edges",
                graph_manager.node_count(), graph_manager.edge_count())
