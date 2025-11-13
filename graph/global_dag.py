"""Global package-level DAG for cross-transaction dependencies.

Tracks dependencies between transactions (main package → third-party packages)
at the package level, without including intra-transaction nodes.
"""

import logging
from typing import Dict, List, Set

import networkx as nx

logger = logging.getLogger("depanalyzer.graph.global_dag")


class GlobalDAG:
    """Package-level dependency DAG across transactions.

    Tracks which transactions depend on which other transactions,
    enabling topological ordering and cycle detection.
    """

    def __init__(self) -> None:
        """Initialize global DAG."""
        self._dag = nx.DiGraph()
        logger.info("Global DAG initialized")

    def add_dependency(self, parent_graph_id: str, child_graph_id: str) -> None:
        """Add dependency edge from parent to child transaction.

        Args:
            parent_graph_id: Parent transaction graph ID.
            child_graph_id: Child transaction graph ID (dependency).
        """
        if not self._dag.has_node(parent_graph_id):
            self._dag.add_node(parent_graph_id)
        if not self._dag.has_node(child_graph_id):
            self._dag.add_node(child_graph_id)

        self._dag.add_edge(parent_graph_id, child_graph_id)
        logger.debug("Added dependency: %s -> %s", parent_graph_id, child_graph_id)

        # Check for cycles
        if not nx.is_directed_acyclic_graph(self._dag):
            logger.warning(
                "Cycle detected in global DAG involving %s and %s",
                parent_graph_id,
                child_graph_id,
            )

    def get_dependencies(self, graph_id: str) -> Set[str]:
        """Get direct dependencies of a transaction.

        Args:
            graph_id: Transaction graph ID.

        Returns:
            Set[str]: Set of child graph IDs.
        """
        if not self._dag.has_node(graph_id):
            return set()
        return set(self._dag.successors(graph_id))

    def get_dependents(self, graph_id: str) -> Set[str]:
        """Get direct dependents of a transaction.

        Args:
            graph_id: Transaction graph ID.

        Returns:
            Set[str]: Set of parent graph IDs.
        """
        if not self._dag.has_node(graph_id):
            return set()
        return set(self._dag.predecessors(graph_id))

    def topological_order(self) -> List[str]:
        """Get topological ordering of transactions.

        Returns:
            List[str]: Graph IDs in topological order (dependencies first).

        Raises:
            nx.NetworkXError: If graph contains cycles.
        """
        try:
            return list(nx.topological_sort(self._dag))
        except nx.NetworkXError as e:
            logger.error("Cannot compute topological order: %s", e)
            raise

    def has_cycle(self) -> bool:
        """Check if global DAG contains cycles.

        Returns:
            bool: True if DAG contains cycles.
        """
        return not nx.is_directed_acyclic_graph(self._dag)

    def get_all_graph_ids(self) -> Set[str]:
        """Get all transaction graph IDs in DAG.

        Returns:
            Set[str]: Set of all graph IDs.
        """
        return set(self._dag.nodes())

    def clear(self) -> None:
        """Clear DAG (for testing)."""
        self._dag.clear()
        logger.debug("Global DAG cleared")
