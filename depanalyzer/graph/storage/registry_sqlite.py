"""SQLite backend for GraphRegistry.

Provides persistent storage for the graph registry using SQLite,
replacing the JSON + file lock approach for better concurrent access.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .sqlite_store import SQLiteStore

logger = logging.getLogger("depanalyzer.graph.storage.registry_sqlite")


class GraphRegistrySQLite(SQLiteStore):
    """SQLite backend for GraphRegistry storage.

    Stores graph metadata (cache paths and summaries) in SQLite.
    """

    def _init_db(self) -> None:
        """Initialize database schema for registry storage."""
        conn = self._get_conn()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS registry (
                graph_id TEXT PRIMARY KEY,
                cache_path TEXT NOT NULL,
                summary TEXT  -- JSON encoded
            );
        """
        )
        logger.debug("GraphRegistry SQLite schema initialized at %s", self._db_path)

    def register(
        self, graph_id: str, cache_path: str, summary: Optional[Dict[str, Any]] = None
    ) -> None:
        """Register a graph with its cache location and summary.

        Args:
            graph_id: Unique graph identifier.
            cache_path: Path to graph cache file.
            summary: Optional graph summary (node counts, artifacts, etc.).
        """
        conn = self._get_conn()
        summary_json = json.dumps(summary) if summary else None
        conn.execute(
            "INSERT OR REPLACE INTO registry (graph_id, cache_path, summary) VALUES (?, ?, ?)",
            (graph_id, str(cache_path), summary_json),
        )
        logger.debug("Registered graph %s in SQLite registry", graph_id)

    def get_entry(self, graph_id: str) -> Optional[Dict[str, Any]]:
        """Get registry entry for a graph.

        Args:
            graph_id: Graph identifier.

        Returns:
            Optional[Dict[str, Any]]: Registry entry or None if not found.
        """
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT cache_path, summary FROM registry WHERE graph_id = ?", (graph_id,)
        )
        row = cursor.fetchone()
        if row:
            return {
                "cache_path": row[0],
                "summary": json.loads(row[1]) if row[1] else {},
            }
        return None

    def get_cache_path(self, graph_id: str) -> Optional[Path]:
        """Get cache path for a graph.

        Args:
            graph_id: Graph identifier.

        Returns:
            Optional[Path]: Cache path or None if not found.
        """
        entry = self.get_entry(graph_id)
        if entry:
            return Path(entry["cache_path"])
        return None

    def get_summary(self, graph_id: str) -> Optional[Dict[str, Any]]:
        """Get summary for a graph.

        Args:
            graph_id: Graph identifier.

        Returns:
            Optional[Dict[str, Any]]: Summary or None if not found.
        """
        entry = self.get_entry(graph_id)
        if entry:
            return entry.get("summary")
        return None

    def has_graph(self, graph_id: str) -> bool:
        """Check if graph is registered.

        Args:
            graph_id: Graph identifier.

        Returns:
            bool: True if graph is registered.
        """
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT 1 FROM registry WHERE graph_id = ? LIMIT 1", (graph_id,)
        )
        return cursor.fetchone() is not None

    def list_graphs(self) -> List[str]:
        """List all registered graph IDs.

        Returns:
            List[str]: List of graph IDs.
        """
        conn = self._get_conn()
        cursor = conn.execute("SELECT graph_id FROM registry")
        return [row[0] for row in cursor]

    def remove(self, graph_id: str) -> bool:
        """Remove a graph from the registry.

        Args:
            graph_id: Graph identifier.

        Returns:
            bool: True if the graph was removed.
        """
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM registry WHERE graph_id = ?", (graph_id,))
        return cursor.rowcount > 0

    def clear(self) -> None:
        """Clear all entries from the registry."""
        conn = self._get_conn()
        conn.execute("DELETE FROM registry")
        logger.debug("GraphRegistry SQLite cleared")

    def count(self) -> int:
        """Get the number of registered graphs.

        Returns:
            int: Number of registered graphs.
        """
        conn = self._get_conn()
        cursor = conn.execute("SELECT COUNT(*) FROM registry")
        return cursor.fetchone()[0]
