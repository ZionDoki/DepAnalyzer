"""Global package-level DAG for cross-transaction dependencies.

Tracks dependencies between transactions (main package → third-party packages)
at the package level, without including intra-transaction nodes.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set

import networkx as nx

logger = logging.getLogger("depanalyzer.graph.global_dag")


class GlobalDAG:
    """Package-level dependency DAG across transactions.

    Tracks which transactions depend on which other transactions,
    enabling topological ordering and cycle detection.

    Singleton pattern ensures all transactions share the same DAG instance.
    """

    _instance: Optional["GlobalDAG"] = None
    _initialized: bool = False

    def __new__(cls, cache_dir: Optional[Path] = None) -> "GlobalDAG":
        """Singleton pattern for global DAG.

        Args:
            cache_dir: Directory for DAG persistence (ignored, passed to __init__).

        Returns:
            GlobalDAG: Singleton instance.
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        """Initialize global DAG.

        Args:
            cache_dir: Directory for DAG persistence. Defaults to .depanalyzer_cache/graphs.
                Only used on first initialization; subsequent calls reuse the
                existing instance and ignore this parameter.
        """
        if GlobalDAG._initialized:
            return

        self._dag = nx.DiGraph()

        # Set up cache directory
        if cache_dir is None:
            cache_dir = Path.cwd() / ".depanalyzer_cache" / "graphs"
        self._cache_dir = cache_dir
        self._dag_file = self._cache_dir / "global_dag.json"

        # Load existing DAG if available
        self._load()

        GlobalDAG._initialized = True
        logger.info("Global DAG initialized (cache: %s)", self._dag_file)

    @classmethod
    def get_instance(cls, cache_dir: Optional[Path] = None) -> "GlobalDAG":
        """Get the singleton GlobalDAG instance.

        Args:
            cache_dir: Optional directory for DAG persistence. Only used on
                first initialization; later calls reuse existing instance.

        Returns:
            GlobalDAG: The singleton instance.
        """
        return cls(cache_dir=cache_dir)

    def _save(self) -> None:
        """Save DAG to disk."""
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

            # Convert DAG to JSON-serializable format
            dag_data = {
                "nodes": list(self._dag.nodes()),
                "edges": list(self._dag.edges()),
            }

            with open(self._dag_file, "w", encoding="utf-8") as f:
                json.dump(dag_data, f, indent=2)

            logger.debug("Saved global DAG to %s", self._dag_file)
        except Exception as e:
            logger.error("Failed to save global DAG: %s", e)

    def _load(self) -> None:
        """Load DAG from disk if it exists."""
        if not self._dag_file.exists():
            logger.debug("No existing global DAG file found at %s", self._dag_file)
            return

        try:
            with open(self._dag_file, "r", encoding="utf-8") as f:
                dag_data = json.load(f)

            # Reconstruct DAG from saved data
            nodes = dag_data.get("nodes", [])
            edges = dag_data.get("edges", [])

            self._dag.add_nodes_from(nodes)
            self._dag.add_edges_from(edges)

            logger.info(
                "Loaded global DAG from %s (%d nodes, %d edges)",
                self._dag_file,
                len(nodes),
                len(edges),
            )
        except Exception as e:
            logger.error("Failed to load global DAG: %s", e)
            # Continue with empty DAG

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

        # Check for cycles and log full cycle path when detected
        if not nx.is_directed_acyclic_graph(self._dag):
            logger.warning(
                "Cycle detected in global DAG involving %s and %s",
                parent_graph_id,
                child_graph_id,
            )

            # Attempt to extract and log the complete cycle chain for diagnostics.
            try:
                raw_cycle = nx.find_cycle(self._dag, source=parent_graph_id)

                if raw_cycle:
                    # raw_cycle is a list of edges: (u, v) or (u, v, orientation)
                    cycle_nodes = []

                    for edge in raw_cycle:
                        # Support both 2-tuple and 3-tuple formats
                        u = edge[0]
                        v = edge[1]

                        if not cycle_nodes:
                            cycle_nodes.append(u)
                        cycle_nodes.append(v)

                    # Normalize cycle so it explicitly shows the closing edge
                    if cycle_nodes and cycle_nodes[0] != cycle_nodes[-1]:
                        cycle_nodes.append(cycle_nodes[0])

                    cycle_repr = " -> ".join(cycle_nodes)
                    logger.warning(
                        "Global DAG cycle path: %s",
                        cycle_repr,
                    )
            except Exception as cycle_err:
                # Cycle introspection is best-effort only and must not break execution.
                logger.debug(
                    "Failed to compute full cycle path for GlobalDAG: %s",
                    cycle_err,
                )

        # Persist to disk after adding dependency
        self._save()

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

    def get_transitive_dependencies(self, graph_id: str) -> Set[str]:
        """Get all transitive dependencies (direct + indirect) of a transaction.

        Args:
            graph_id: Transaction graph ID.

        Returns:
            Set[str]: Set of all dependent graph IDs (transitive closure).
        """
        if not self._dag.has_node(graph_id):
            return set()

        try:
            # Use NetworkX descendants to get all reachable nodes
            return nx.descendants(self._dag, graph_id)
        except nx.NetworkXError as e:
            logger.warning(
                "Failed to compute transitive dependencies for %s: %s", graph_id, e
            )
            return set()

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
