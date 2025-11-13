"""Dead code detection via reachability analysis."""

import logging
from typing import List, Set

from graph.manager import EdgeKind, GraphManager, NodeType

logger = logging.getLogger("depanalyzer.analysis.deadcode")


class DeadcodeAnalyzer:
    """Analyzer for detecting unreachable code/assets.

    Performs reverse reachability from final packaging targets
    to identify dead code and unused dependencies.
    """

    def __init__(self, graph_manager: GraphManager) -> None:
        """Initialize deadcode analyzer.

        Args:
            graph_manager: Transaction graph manager.
        """
        self.graph_manager = graph_manager
        logger.info("DeadcodeAnalyzer initialized")

    def analyze(self) -> Set[str]:
        """Perform deadcode analysis.

        Returns:
            Set[str]: Set of unreachable node IDs.
        """
        logger.info("Performing deadcode analysis")

        # Find final packaging targets as roots
        roots = self._find_packaging_roots()
        logger.info("Found %d packaging roots", len(roots))

        # Compute reachable nodes via reverse traversal
        reachable = self._compute_reachable(roots)
        logger.info("Found %d reachable nodes", len(reachable))

        # Compute unreachable nodes
        all_nodes = set(node_id for node_id, _ in self.graph_manager.nodes())
        unreachable = all_nodes - reachable

        logger.info("Identified %d unreachable nodes", len(unreachable))
        return unreachable

    def _find_packaging_roots(self) -> List[str]:
        """Find final packaging target nodes.

        Returns:
            List[str]: List of packaging target node IDs.
        """
        roots = []
        for node_id, attrs in self.graph_manager.nodes():
            node_type = attrs.get("type")
            # Consider HAP/HAR/HSP as roots
            if node_type in ["hap", "har", "hsp", "executable"]:
                roots.append(node_id)
        return roots

    def _compute_reachable(self, roots: List[str]) -> Set[str]:
        """Compute reachable nodes via reverse DFS from roots.

        Args:
            roots: List of root node IDs.

        Returns:
            Set[str]: Set of reachable node IDs.
        """
        reachable = set()
        stack = list(roots)

        while stack:
            current = stack.pop()
            if current in reachable:
                continue

            reachable.add(current)

            # Add predecessors (dependencies)
            for pred in self.graph_manager._backend.predecessors(current):
                if pred not in reachable:
                    stack.append(pred)

        return reachable
