"""JSON export for transaction graphs."""

import json
import logging
from pathlib import Path

import networkx as nx

from depanalyzer.graph.manager import GraphManager

logger = logging.getLogger("depanalyzer.export.json")


def export_json(graph_manager: GraphManager, output_path: Path) -> None:
    """Export graph to JSON format.

    Args:
        graph_manager: Graph manager to export.
        output_path: Output file path.
    """
    logger.info("Exporting graph to JSON: %s", output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    data = nx.readwrite.json_graph.node_link_data(
        graph_manager._backend.native_graph, edges="edges"
    )

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info("JSON export completed: %d nodes, %d edges",
                graph_manager.node_count(), graph_manager.edge_count())
