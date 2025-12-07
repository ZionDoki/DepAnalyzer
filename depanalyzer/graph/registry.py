"""Global graph registry for cross-transaction graph management.

Registry maintains mapping of graph_id to disk cache location and summary,
enabling cross-transaction graph queries without loading full graph bodies.

Uses SQLite for persistent storage with WAL mode for concurrent access.
"""

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("depanalyzer.graph.registry")


class GraphRegistry:
    """Global registry for graph snapshots and summaries.

    Maps graph_id to disk cache location and metadata, enabling
    parent transactions to reference child graphs without loading them.

    Uses SQLite for persistent storage with thread-local connections.
    """

    _instance: Optional["GraphRegistry"] = None
    _initialized: bool = False
    _instance_lock = threading.Lock()  # Protect singleton creation and reconfiguration
    _connections: List[sqlite3.Connection] = []  # Track all connections for cleanup
    _conn_lock = threading.Lock()  # Protect connection list

    def __new__(cls, cache_root: Optional[Path] = None) -> "GraphRegistry":
        """Singleton pattern for global registry."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, cache_root: Optional[Path] = None) -> None:
        """Initialize registry with SQLite backend.

        Args:
            cache_root: Optional cache root directory.
        """
        # Note: Don't acquire _instance_lock here - it's already held by get_instance()
        # or we're being called directly (which is discouraged)
        if GraphRegistry._initialized:
            return

        self.cache_root = Path(cache_root) if cache_root else Path(".depanalyzer_cache/graphs")
        self.cache_root.mkdir(parents=True, exist_ok=True)

        self._db_path = self.cache_root / "registry.db"
        self._local = threading.local()
        self._init_db()

        GraphRegistry._initialized = True
        logger.info(
            "Graph registry initialized (SQLite: %s, PID: %d)",
            self._db_path,
            os.getpid(),
        )

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local SQLite connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(
                str(self._db_path),
                timeout=30.0,
                isolation_level=None,
            )
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                pass
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=-64000")
            self._local.conn = conn
            # Track connection for cleanup
            with GraphRegistry._conn_lock:
                GraphRegistry._connections.append(conn)
        return self._local.conn

    def _init_db(self) -> None:
        """Initialize SQLite schema."""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS registry (
                graph_id TEXT PRIMARY KEY,
                cache_path TEXT NOT NULL,
                summary TEXT
            )
        """)

    @classmethod
    def get_instance(cls, cache_root: Optional[Path] = None) -> "GraphRegistry":
        """Get the singleton GraphRegistry instance.

        Thread-safe singleton access with optional reconfiguration.
        """
        with cls._instance_lock:
            if cls._instance is None:
                return cls(cache_root=cache_root)

            instance = cls._instance
            if cache_root is not None:
                cache_root = Path(cache_root)
                if cache_root != instance.cache_root:
                    instance.configure_cache_root(cache_root)
                    logger.info("Reconfigured GraphRegistry: %s", instance._db_path)

            return instance

    def configure_cache_root(self, cache_root: Path) -> None:
        """Configure registry to use a new cache root."""
        self.cache_root = Path(cache_root)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._db_path = self.cache_root / "registry.db"
        self._local = threading.local()
        self._init_db()

    def register(
        self,
        graph_id: str,
        cache_path: Path,
        summary: Dict[str, Any],
    ) -> None:
        """Register a graph with its cache location and summary."""
        try:
            conn = self._get_conn()
            summary_json = json.dumps(summary) if summary else None
            conn.execute(
                "INSERT OR REPLACE INTO registry (graph_id, cache_path, summary) VALUES (?, ?, ?)",
                (graph_id, str(cache_path), summary_json),
            )
            logger.info("Registered graph: %s (PID: %d)", graph_id, os.getpid())
        except sqlite3.Error as e:
            logger.error("Failed to register graph %s: %s", graph_id, e)

    def get_entry(self, graph_id: str) -> Optional[Dict[str, Any]]:
        """Get registry entry for a graph."""
        try:
            conn = self._get_conn()
            cursor = conn.execute(
                "SELECT cache_path, summary FROM registry WHERE graph_id = ?",
                (graph_id,),
            )
            row = cursor.fetchone()
            if row:
                return {
                    "cache_path": row[0],
                    "summary": json.loads(row[1]) if row[1] else {},
                }
            return None
        except sqlite3.Error as e:
            logger.warning("Failed to get entry for %s: %s", graph_id, e)
            return None

    def get_cache_path(self, graph_id: str) -> Optional[Path]:
        """Get cache path for a graph."""
        entry = self.get_entry(graph_id)
        if entry:
            return Path(entry["cache_path"])
        return None

    def get_summary(self, graph_id: str) -> Optional[Dict[str, Any]]:
        """Get summary for a graph."""
        entry = self.get_entry(graph_id)
        if entry:
            return entry.get("summary")
        return None

    def has_graph(self, graph_id: str) -> bool:
        """Check if graph is registered."""
        try:
            conn = self._get_conn()
            cursor = conn.execute(
                "SELECT 1 FROM registry WHERE graph_id = ? LIMIT 1",
                (graph_id,),
            )
            return cursor.fetchone() is not None
        except sqlite3.Error as e:
            logger.warning("Failed to check graph %s: %s", graph_id, e)
            return False

    def list_graphs(self) -> List[str]:
        """List all registered graph IDs."""
        try:
            conn = self._get_conn()
            cursor = conn.execute("SELECT graph_id FROM registry")
            return [row[0] for row in cursor]
        except sqlite3.Error as e:
            logger.warning("Failed to list graphs: %s", e)
            return []

    def clear(self) -> None:
        """Clear registry (for testing)."""
        try:
            conn = self._get_conn()
            conn.execute("DELETE FROM registry")
            logger.warning("Registry cleared")
        except sqlite3.Error as e:
            logger.error("Failed to clear registry: %s", e)

    @classmethod
    def shutdown(cls) -> None:
        """Reset singleton state and close all connections.

        This method closes all tracked SQLite connections across all threads,
        then resets the singleton state. Safe to call multiple times.
        """
        # Close all tracked connections
        with cls._conn_lock:
            for conn in cls._connections:
                try:
                    conn.close()
                except Exception:
                    pass
            cls._connections.clear()

        # Reset singleton state
        if cls._instance is not None:
            try:
                if hasattr(cls._instance, "_local"):
                    cls._instance._local = threading.local()
            except Exception:
                pass

        logger.info("GraphRegistry shutdown complete")
        cls._instance = None
        cls._initialized = False
