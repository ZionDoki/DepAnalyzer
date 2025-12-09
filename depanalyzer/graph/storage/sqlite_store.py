"""SQLite-based storage base class for cross-process data sharing.

Provides thread-safe SQLite storage with WAL mode support for improved
concurrent read/write performance.
"""

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Set

logger = logging.getLogger("depanalyzer.graph.storage.sqlite_store")


class SQLiteStore:
    """Thread-safe SQLite storage with WAL mode support.

    Each thread gets its own connection to avoid SQLite threading issues.
    WAL mode is used when available for better concurrent access.
    """

    def __init__(self, db_path: Path):
        """Initialize SQLite store.

        Args:
            db_path: Path to the SQLite database file.
        """
        self._db_path = Path(db_path)
        self._local = threading.local()
        self._connections: Set[sqlite3.Connection] = set()
        self._conn_lock = threading.Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local connection.

        Returns:
            sqlite3.Connection: Thread-local database connection.
        """
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(
                str(self._db_path),
                timeout=30.0,
                isolation_level=None,  # Manual transaction control
            )
            try:
                # Configure connection - if any step fails, close the connection
                # Try WAL mode, fall back to DELETE if not supported
                try:
                    result = conn.execute("PRAGMA journal_mode=WAL").fetchone()
                    if result and result[0].upper() != "WAL":
                        logger.debug("WAL mode not available, using default journal mode")
                except sqlite3.OperationalError:
                    logger.debug("Failed to set WAL mode, using default")

                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
                conn.row_factory = sqlite3.Row

                # Track connection BEFORE assigning to thread-local
                # This ensures the connection is tracked even if assignment fails
                with self._conn_lock:
                    self._connections.add(conn)

                self._local.conn = conn
                logger.debug("Created new SQLite connection for thread %s", threading.current_thread().name)
            except Exception:
                # If configuration fails, close the connection to prevent leak
                conn.close()
                raise

        return self._local.conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Context manager for explicit transactions.

        Uses IMMEDIATE mode to acquire write lock at transaction start,
        preventing deadlocks in concurrent scenarios.

        Yields:
            sqlite3.Connection: The database connection within a transaction.
        """
        conn = self._get_conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def _init_db(self) -> None:
        """Initialize database schema.

        Override in subclasses to create tables and indexes.
        """
        pass

    def close(self) -> None:
        """Close thread-local connection."""
        if hasattr(self._local, "conn") and self._local.conn:
            try:
                self._local.conn.close()
                with self._conn_lock:
                    self._connections.discard(self._local.conn)
            except sqlite3.Error as e:
                logger.debug("Error closing SQLite connection: %s", e)
            self._local.conn = None

    def close_all(self) -> None:
        """Close all tracked SQLite connections."""
        with self._conn_lock:
            conns = list(self._connections)
            self._connections.clear()

        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error as e:
                logger.debug("Error closing SQLite connection during close_all: %s", e)

    def __del__(self) -> None:
        """Ensure connections are cleaned up when the store is garbage collected."""
        try:
            self.close_all()
        except Exception:
            # Destructors should never raise.
            return

    @property
    def db_path(self) -> Path:
        """Return the database file path."""
        return self._db_path
