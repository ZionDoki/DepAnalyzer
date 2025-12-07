"""SQLite backend for GlobalDAG.

Provides persistent storage for the global package-level DAG using SQLite,
replacing the JSON + file lock approach for better concurrent access.
"""

import logging
from pathlib import Path
from typing import List, Set, Tuple

from .sqlite_store import SQLiteStore

logger = logging.getLogger("depanalyzer.graph.storage.global_dag_sqlite")


class GlobalDAGSQLite(SQLiteStore):
    """SQLite backend for GlobalDAG storage.

    Stores graph nodes and edges in SQLite tables with proper indexing
    for efficient queries.
    """

    def _init_db(self) -> None:
        """Initialize database schema for DAG storage."""
        conn = self._get_conn()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                graph_id TEXT PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS edges (
                parent_id TEXT NOT NULL,
                child_id TEXT NOT NULL,
                PRIMARY KEY (parent_id, child_id)
            );

            CREATE INDEX IF NOT EXISTS idx_edges_parent ON edges(parent_id);
            CREATE INDEX IF NOT EXISTS idx_edges_child ON edges(child_id);
        """
        )
        logger.debug("GlobalDAG SQLite schema initialized at %s", self._db_path)

    def add_node(self, graph_id: str) -> None:
        """Add a node to the DAG.

        Args:
            graph_id: The graph ID to add.
        """
        conn = self._get_conn()
        conn.execute("INSERT OR IGNORE INTO nodes (graph_id) VALUES (?)", (graph_id,))

    def has_node(self, graph_id: str) -> bool:
        """Check if a node exists in the DAG.

        Args:
            graph_id: The graph ID to check.

        Returns:
            bool: True if the node exists.
        """
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT 1 FROM nodes WHERE graph_id = ? LIMIT 1", (graph_id,)
        )
        return cursor.fetchone() is not None

    def add_edge(self, parent_id: str, child_id: str) -> None:
        """Add an edge from parent to child.

        Also ensures both nodes exist.

        Args:
            parent_id: Parent graph ID.
            child_id: Child graph ID (dependency).
        """
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO nodes (graph_id) VALUES (?)", (parent_id,)
            )
            conn.execute(
                "INSERT OR IGNORE INTO nodes (graph_id) VALUES (?)", (child_id,)
            )
            conn.execute(
                "INSERT OR IGNORE INTO edges (parent_id, child_id) VALUES (?, ?)",
                (parent_id, child_id),
            )

    def remove_edges_from_parent(self, parent_id: str) -> int:
        """Remove all outgoing edges from a parent node.

        Args:
            parent_id: Parent graph ID.

        Returns:
            int: Number of edges removed.
        """
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM edges WHERE parent_id = ?", (parent_id,)
        )
        return cursor.rowcount

    def get_successors(self, graph_id: str) -> Set[str]:
        """Get direct children (dependencies) of a node.

        Args:
            graph_id: The graph ID to query.

        Returns:
            Set[str]: Set of child graph IDs.
        """
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT child_id FROM edges WHERE parent_id = ?", (graph_id,)
        )
        return {row[0] for row in cursor}

    def get_predecessors(self, graph_id: str) -> Set[str]:
        """Get direct parents (dependents) of a node.

        Args:
            graph_id: The graph ID to query.

        Returns:
            Set[str]: Set of parent graph IDs.
        """
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT parent_id FROM edges WHERE child_id = ?", (graph_id,)
        )
        return {row[0] for row in cursor}

    def get_all_nodes(self) -> Set[str]:
        """Get all nodes in the DAG.

        Returns:
            Set[str]: Set of all graph IDs.
        """
        conn = self._get_conn()
        cursor = conn.execute("SELECT graph_id FROM nodes")
        return {row[0] for row in cursor}

    def get_all_edges(self) -> List[Tuple[str, str]]:
        """Get all edges in the DAG.

        Returns:
            List[Tuple[str, str]]: List of (parent_id, child_id) tuples.
        """
        conn = self._get_conn()
        cursor = conn.execute("SELECT parent_id, child_id FROM edges")
        return [(row[0], row[1]) for row in cursor]

    def clear(self) -> None:
        """Clear all data from the DAG."""
        with self.transaction() as conn:
            conn.execute("DELETE FROM edges")
            conn.execute("DELETE FROM nodes")
        logger.debug("GlobalDAG SQLite cleared")

    def node_count(self) -> int:
        """Get the number of nodes in the DAG.

        Returns:
            int: Number of nodes.
        """
        conn = self._get_conn()
        cursor = conn.execute("SELECT COUNT(*) FROM nodes")
        return cursor.fetchone()[0]

    def edge_count(self) -> int:
        """Get the number of edges in the DAG.

        Returns:
            int: Number of edges.
        """
        conn = self._get_conn()
        cursor = conn.execute("SELECT COUNT(*) FROM edges")
        return cursor.fetchone()[0]
