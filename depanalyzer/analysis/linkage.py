"""Linkage analysis for determining static/dynamic/module dependencies."""

import logging
from typing import Dict, List, Set

from depanalyzer.graph.manager import EdgeKind, GraphManager

logger = logging.getLogger("depanalyzer.analysis.linkage")


class LinkageAnalyzer:
    """Analyzer for determining linkage types and confidence.

    Analyzes link edges to determine whether dependencies are
    static, dynamic, or module-based, with confidence scoring.
    """

    def __init__(self, graph_manager: GraphManager) -> None:
        """Initialize linkage analyzer.

        Args:
            graph_manager: Transaction graph manager.
        """
        self.graph_manager = graph_manager
        logger.info("LinkageAnalyzer initialized")

    def analyze(self) -> Dict[str, str]:
        """Perform linkage analysis.

        Returns:
            Dict[str, str]: Map of edge_id to linkage_type.
        """
        logger.info("Performing linkage analysis")

        linkage_map = {}

        for source, target, key, attrs in self.graph_manager.edges():
            edge_kind = attrs.get("kind")
            if edge_kind == EdgeKind.LINKS or edge_kind == "link_libraries":
                edge_id = f"{source}->{target}:{key}"
                linkage_type = self._infer_linkage_type(target, attrs)
                linkage_map[edge_id] = linkage_type

        logger.info("Analyzed %d link edges", len(linkage_map))
        return linkage_map

    def _infer_linkage_type(self, target: str, edge_attrs: Dict) -> str:
        """Infer linkage type from edge attributes and target node.

        Args:
            target: Target node ID.
            edge_attrs: Edge attributes.

        Returns:
            str: Linkage type ('static', 'dynamic', 'module', 'unknown').
        """
        # Check if linkage type is explicitly specified
        explicit_linkage = edge_attrs.get("linkage_type")
        if explicit_linkage:
            return explicit_linkage

        # Check over_approx flag
        over_approx = edge_attrs.get("over_approx", False)

        # Check confidence
        confidence = edge_attrs.get("confidence", 1.0)

        # If over-approximated or low confidence, mark as unknown
        if over_approx or confidence < 0.5:
            return "unknown"

        # Get target node attributes to infer linkage type
        target_attrs = self.graph_manager.get_node_attributes(target)
        if not target_attrs:
            return "unknown"

        target_type = target_attrs.get("type")
        target_linkage_kind = target_attrs.get("linkage_kind")

        # Infer based on target type
        if target_type == "shared_library" or target_linkage_kind == "shared":
            return "dynamic"
        elif target_type == "static_library" or target_linkage_kind == "static":
            return "static"
        elif target_type == "module" or target_linkage_kind == "module":
            return "module"
        elif target_type in ["hap", "har", "hsp"]:
            # OpenHarmony packaging - typically dynamic
            return "dynamic"
        else:
            return "unknown"
