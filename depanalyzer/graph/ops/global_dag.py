"""Global package-level DAG for cross-transaction dependencies.

Tracks dependencies between transactions (main package -> third-party
packages) at the package level, without including intra-transaction nodes.

Uses SQLite for persistent storage with WAL mode for concurrent access.
"""

import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import List, Optional, Set, Tuple

import networkx as nx
from networkx.exception import NetworkXError

from ..io import break_cycles
from ..models.schema import NodeType
from .condensation import CondensationResult, build_condensation_dag

logger = logging.getLogger("depanalyzer.graph.ops.global_dag")

GRAPH_ERRORS = (sqlite3.Error, NetworkXError, OSError, ValueError)
CYCLE_ERRORS = (NetworkXError, ValueError)


class GlobalDAG:
    """Package-level dependency DAG across transactions.

    Tracks which transactions depend on which other transactions,
    enabling topological ordering and cycle detection.

    Uses SQLite for persistent storage with thread-local connections.
    Singleton pattern ensures all transactions in a given process share
    the same DAG instance.
    """

    _instance = None
    _initialized = False
    _instance_lock = threading.Lock()  # Protect singleton creation and reconfiguration
    _connections: List[sqlite3.Connection] = []  # Track all connections for cleanup
    _conn_lock = threading.Lock()  # Protect connection list

    def __new__(cls, cache_dir: Optional[Path] = None) -> "GlobalDAG":
        """Singleton pattern for global DAG within a single process."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        """Initialize global DAG with SQLite backend.

        Args:
            cache_dir: Directory for DAG persistence. Defaults to
                .depanalyzer_cache/graphs in current working directory.
        """
        # Note: Don't acquire _instance_lock here - it's already held by get_instance()
        # or we're being called directly (which is discouraged)
        if GlobalDAG._initialized:
            return

        self._check_on_write = os.getenv(
            "DEPANALYZER_DAG_CHECK_ON_WRITE", "1"
        ).lower() not in ("0", "false", "no")

        if cache_dir is None:
            cache_dir = Path.cwd() / ".depanalyzer_cache" / "graphs"
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._db_path = self.cache_dir / "global_dag.db"
        self._local = threading.local()
        self._init_db()

        GlobalDAG._initialized = True
        logger.info("Global DAG initialized (SQLite: %s)", self._db_path)

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
            with GlobalDAG._conn_lock:
                GlobalDAG._connections.append(conn)
        return self._local.conn

    def _init_db(self) -> None:
        """Initialize SQLite schema."""
        conn = self._get_conn()
        conn.executescript("""
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
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            );
            INSERT OR IGNORE INTO meta (key, value) VALUES ('data_version', 0);
        """)

    def _get_db_version(self) -> int:
        """Get current data version from database for cross-process cache invalidation."""
        conn = self._get_conn()
        cursor = conn.execute("SELECT value FROM meta WHERE key = 'data_version'")
        row = cursor.fetchone()
        return row[0] if row else 0

    def _increment_db_version(self, conn: sqlite3.Connection) -> None:
        """Increment data version in database (call within transaction)."""
        conn.execute("UPDATE meta SET value = value + 1 WHERE key = 'data_version'")

    @classmethod
    def get_instance(cls, cache_dir: Optional[Path] = None) -> "GlobalDAG":
        """Get the singleton GlobalDAG instance.

        Thread-safe singleton access with optional reconfiguration.
        """
        with cls._instance_lock:
            if cls._instance is None:
                return cls(cache_dir=cache_dir)

            instance = cls._instance
            if cache_dir is not None:
                cache_dir = Path(cache_dir)
                if cache_dir != instance.cache_dir:
                    instance.cache_dir = cache_dir
                    instance.cache_dir.mkdir(parents=True, exist_ok=True)
                    instance._db_path = cache_dir / "global_dag.db"
                    instance._local = threading.local()
                    instance._init_db()
                    logger.info("Reconfigured GlobalDAG: %s", instance._db_path)

            return instance

    def _build_networkx_dag(self) -> nx.DiGraph:
        """Build NetworkX DiGraph from SQLite data for graph algorithms."""
        conn = self._get_conn()
        dag = nx.DiGraph()

        for row in conn.execute("SELECT graph_id FROM nodes"):
            dag.add_node(row[0])

        for row in conn.execute("SELECT parent_id, child_id FROM edges"):
            dag.add_edge(row[0], row[1])

        return dag

    def _get_networkx_dag(self) -> nx.DiGraph:
        """Get cached NetworkX DAG, rebuilding if cache is stale.

        Uses thread-local caching with DB-based version invalidation to ensure
        cross-process cache coherence. Each read checks the database version
        to detect writes from other processes.
        """
        # Initialize thread-local cache if needed
        if not hasattr(self._local, "nx_cache"):
            self._local.nx_cache = None
            self._local.nx_cache_version = -1

        # Check DB version for cross-process invalidation
        db_version = self._get_db_version()

        # Rebuild cache if stale
        if (self._local.nx_cache is None or
                self._local.nx_cache_version != db_version):
            self._local.nx_cache = self._build_networkx_dag()
            self._local.nx_cache_version = db_version

        return self._local.nx_cache

    def add_dependency(self, parent_graph_id: str, child_graph_id: str) -> None:
        """Add dependency edge from parent to child transaction."""
        try:
            conn = self._get_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO nodes (graph_id) VALUES (?)",
                    (parent_graph_id,),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO nodes (graph_id) VALUES (?)",
                    (child_graph_id,),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO edges (parent_id, child_id) VALUES (?, ?)",
                    (parent_graph_id, child_graph_id),
                )
                # Increment DB version for cross-process cache invalidation
                self._increment_db_version(conn)
                conn.execute("COMMIT")

                logger.debug("Added dependency: %s -> %s", parent_graph_id, child_graph_id)

                if self._check_on_write:
                    dag = self._get_networkx_dag()
                    if not nx.is_directed_acyclic_graph(dag):
                        self._log_cycle(dag, parent_graph_id, child_graph_id)

            except Exception:
                conn.execute("ROLLBACK")
                raise

        except GRAPH_ERRORS as e:
            logger.error(
                "Failed to add dependency %s -> %s: %s",
                parent_graph_id, child_graph_id, e
            )

    def _log_cycle(self, dag: nx.DiGraph, parent: str, child: str) -> None:
        """Log cycle detection warning."""
        logger.warning("Cycle detected in global DAG involving %s and %s", parent, child)
        try:
            raw_cycle = nx.find_cycle(dag, source=parent)
            if raw_cycle:
                cycle_nodes = [raw_cycle[0][0]]
                for edge in raw_cycle:
                    cycle_nodes.append(edge[1])
                logger.warning("Global DAG cycle path: %s", " -> ".join(cycle_nodes))
        except CYCLE_ERRORS:
            pass

    def replace_dependencies(self, parent_graph_id: str, new_children: Set[str]) -> None:
        """Replace all outgoing dependencies of a parent graph."""
        try:
            conn = self._get_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Get old count for logging
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM edges WHERE parent_id = ?",
                    (parent_graph_id,),
                )
                old_count = cursor.fetchone()[0]

                # Ensure parent exists
                conn.execute(
                    "INSERT OR IGNORE INTO nodes (graph_id) VALUES (?)",
                    (parent_graph_id,),
                )

                # Remove old edges
                conn.execute(
                    "DELETE FROM edges WHERE parent_id = ?",
                    (parent_graph_id,),
                )

                # Add new edges
                for child_id in new_children:
                    conn.execute(
                        "INSERT OR IGNORE INTO nodes (graph_id) VALUES (?)",
                        (child_id,),
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO edges (parent_id, child_id) VALUES (?, ?)",
                        (parent_graph_id, child_id),
                    )

                # Increment DB version for cross-process cache invalidation
                self._increment_db_version(conn)
                conn.execute("COMMIT")

                logger.info(
                    "Replaced dependencies for %s: %d previous -> %d new",
                    parent_graph_id, old_count, len(new_children)
                )

                if self._check_on_write:
                    dag = self._get_networkx_dag()
                    if not nx.is_directed_acyclic_graph(dag):
                        logger.warning(
                            "Cycle detected after replacing dependencies for %s",
                            parent_graph_id
                        )

            except Exception:
                conn.execute("ROLLBACK")
                raise

        except GRAPH_ERRORS as e:
            logger.error("Failed to replace dependencies for %s: %s", parent_graph_id, e)

    def get_dependencies(self, graph_id: str) -> Set[str]:
        """Get direct dependencies of a transaction."""
        try:
            conn = self._get_conn()
            cursor = conn.execute(
                "SELECT child_id FROM edges WHERE parent_id = ?",
                (graph_id,),
            )
            return {row[0] for row in cursor}
        except GRAPH_ERRORS as e:
            logger.warning("Failed to get dependencies for %s: %s", graph_id, e)
            return set()

    def get_transitive_dependencies(self, graph_id: str) -> Set[str]:
        """Get all transitive dependencies of a transaction."""
        try:
            dag = self._get_networkx_dag()
            if not dag.has_node(graph_id):
                return set()
            return nx.descendants(dag, graph_id)
        except GRAPH_ERRORS as e:
            logger.warning("Failed to compute transitive dependencies for %s: %s", graph_id, e)
            return set()

    def get_dependents(self, graph_id: str) -> Set[str]:
        """Get direct dependents of a transaction."""
        try:
            conn = self._get_conn()
            cursor = conn.execute(
                "SELECT parent_id FROM edges WHERE child_id = ?",
                (graph_id,),
            )
            return {row[0] for row in cursor}
        except GRAPH_ERRORS as e:
            logger.warning("Failed to get dependents for %s: %s", graph_id, e)
            return set()

    def get_all_graph_ids(self) -> Set[str]:
        """Get all transaction graph IDs in DAG."""
        try:
            conn = self._get_conn()
            cursor = conn.execute("SELECT graph_id FROM nodes")
            return {row[0] for row in cursor}
        except GRAPH_ERRORS as e:
            logger.warning("Failed to get all graph IDs: %s", e)
            return set()

    def has_cycle(self) -> bool:
        """Check if global DAG contains cycles."""
        try:
            dag = self._get_networkx_dag()
            return not nx.is_directed_acyclic_graph(dag)
        except GRAPH_ERRORS:
            return False

    def get_cycles(self, limit: Optional[int] = None) -> List[List[str]]:
        """Enumerate cycles present in the GlobalDAG."""
        cycles: List[List[str]] = []
        try:
            dag = self._get_networkx_dag()
            max_cycles = limit if limit and limit > 0 else None

            for cycle in nx.simple_cycles(dag):
                cycles.append([str(node) for node in cycle])
                if max_cycles and len(cycles) >= max_cycles:
                    break

        except GRAPH_ERRORS as e:
            logger.warning("Failed to get cycles: %s", e)

        return cycles

    def topological_order(self) -> List[str]:
        """Get topological ordering of transactions."""
        dag = self._get_networkx_dag()
        try:
            return list(nx.topological_sort(dag))
        except nx.NetworkXError as e:
            logger.error("Cannot compute topological order: %s", e)
            raise

    def get_acyclic_view(self) -> Tuple[nx.MultiDiGraph, int]:
        """Return a virtualized, acyclic view of the global DAG."""
        try:
            dag = self._get_networkx_dag()
            as_multi = nx.MultiDiGraph()
            as_multi.add_nodes_from(dag.nodes(data=True))
            for u, v, data in dag.edges(data=True):
                as_multi.add_edge(u, v, **(data or {}))
            return break_cycles(as_multi)
        except GRAPH_ERRORS as e:
            logger.warning("Failed to build acyclic view: %s", e)
            return nx.MultiDiGraph(), 0

    def build_cluster_view(self) -> CondensationResult:
        """Construct a condensation DAG for the global graph."""
        dag = self._get_networkx_dag()
        return build_condensation_dag(
            dag,
            node_prefix="global_scc:",
            cluster_type=NodeType.SCC_CLUSTER.value,
        )

    def reload_from_disk(self) -> None:
        """Reload DAG - no-op for SQLite (always reads from DB)."""
        pass

    def clear(self) -> None:
        """Clear DAG (for testing)."""
        try:
            conn = self._get_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("DELETE FROM edges")
                conn.execute("DELETE FROM nodes")
                # Increment DB version for cross-process cache invalidation
                self._increment_db_version(conn)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            logger.debug("Global DAG cleared")
        except GRAPH_ERRORS as e:
            logger.error("Failed to clear GlobalDAG: %s", e)

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
                except Exception as exc:  # pragma: no cover - defensive cleanup
                    logger.debug("Error closing GlobalDAG connection: %s", exc)
            cls._connections.clear()

        # Reset singleton state
        if cls._instance is not None:
            try:
                if hasattr(cls._instance, "_local"):
                    cls._instance._local = threading.local()
            except Exception as exc:  # pragma: no cover - defensive cleanup
                logger.debug("Error resetting GlobalDAG thread-local: %s", exc)

        logger.info("GlobalDAG shutdown complete")
        cls._instance = None
        cls._initialized = False
