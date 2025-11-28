"""Dead code detection via reachability analysis."""

import logging
from typing import List, Set

from depanalyzer.graph import GraphManager
from depanalyzer.graph.models.schema import EdgeKind

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
        graph = self.graph_manager.backend.native_graph
        stack = list(roots)

        while stack:
            current = stack.pop()
            if current in reachable:
                continue

            reachable.add(current)

            # Traverse forward along all outgoing edges (consumer -> dependency).
            for succ in graph.successors(current):
                if succ not in reachable:
                    stack.append(succ)

            # Allow reachability to flow from members to their SCC/cluster
            # containers via `contains` edges (container -> member).
            for pred in graph.predecessors(current):
                if pred in reachable:
                    continue

                edge_data = graph.get_edge_data(pred, current) or {}
                if any((attrs or {}).get("kind") == EdgeKind.CONTAINS.value for attrs in edge_data.values()):
                    stack.append(pred)

        return reachable
