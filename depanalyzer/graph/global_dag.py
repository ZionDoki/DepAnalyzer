"""Global package-level DAG for cross-transaction dependencies.

Tracks dependencies between transactions (main package -> third-party
packages) at the package level, without including intra-transaction nodes.

The implementation is explicitly hardened for multi-process usage:
- All reads and writes go through a file-level lock so that concurrent
  scans updating the same DAG do not corrupt the on-disk JSON.
- Each write operation performs a load-merge-save cycle under the lock,
  ensuring that edges from different processes are not lost.
"""

# File I/O paths deliberately catch Exception to keep cache handling resilient.


import json
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional, Set

import networkx as nx
from networkx.exception import NetworkXError

from depanalyzer.graph.condensation import CondensationResult, build_condensation_dag
from depanalyzer.graph.schema import NodeType

logger = logging.getLogger("depanalyzer.graph.global_dag")

IO_ERRORS = (OSError, TimeoutError)
STATE_ERRORS = IO_ERRORS + (json.JSONDecodeError, ValueError)
GRAPH_ERRORS = STATE_ERRORS + (NetworkXError,)
CYCLE_ERRORS = (NetworkXError, ValueError)

try:
    import fcntl

    _HAS_FCNTL = True
except ImportError:
    fcntl = None
    _HAS_FCNTL = False


class GlobalDAG:
    """Package-level dependency DAG across transactions.

    Tracks which transactions depend on which other transactions,
    enabling topological ordering and cycle detection.

    Singleton pattern ensures all transactions in a given process share
    the same DAG instance. Cross-process coordination is done via a
    file-level lock around the on-disk JSON representation.
    """

    _instance = None
    _initialized = False

    def __new__(cls, cache_dir: Optional[Path] = None) -> "GlobalDAG":
        """Singleton pattern for global DAG within a single process.

        Args:
            cache_dir: Directory for DAG persistence (ignored on reuse).

        Returns:
            GlobalDAG: Singleton instance.
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        """Initialize global DAG and load from disk once per process.

        Args:
            cache_dir: Directory for DAG persistence. Defaults to
                .depanalyzer_cache/graphs in current working directory.
                Only used on first initialization; subsequent calls reuse
                the existing instance and ignore this parameter.
        """
        if GlobalDAG._initialized:
            return

        self._dag = nx.DiGraph()
        # Whether to perform DAG-wide cycle checks on each write operation.
        # This can be disabled via environment variable when working with very
        # large DAGs to avoid the O(V+E) cost on every update.
        self._check_on_write = os.getenv(
            "DEPANALYZER_DAG_CHECK_ON_WRITE", "1"
        ).lower() not in ("0", "false", "no")

        # Set up cache directory and DAG file paths
        if cache_dir is None:
            cache_dir = Path.cwd() / ".depanalyzer_cache" / "graphs"
        self.cache_dir = cache_dir
        self.dag_file = self.cache_dir / "global_dag.json"

        # Load existing DAG if available (under lock)
        self._load()

        GlobalDAG._initialized = True
        logger.info("Global DAG initialized (cache: %s)", self.dag_file)

    @classmethod
    def get_instance(cls, cache_dir: Optional[Path] = None) -> "GlobalDAG":
        """Get the singleton GlobalDAG instance for the current process.

        Args:
            cache_dir: Optional directory for DAG persistence. When provided
                and different from the currently configured cache_dir, the
                singleton will be reconfigured to use the new location and
                reload its state from disk.

        Returns:
            GlobalDAG: The singleton instance.
        """
        # First-time initialization.
        if cls._instance is None:
            return cls(cache_dir=cache_dir)

        instance: "GlobalDAG" = cls._instance

        # Handle cache-dir reconfiguration on reuse so that callers such as
        # CLI commands that pass an explicit --cache-dir do not silently
        # operate on a previously initialized default location.
        if cache_dir is not None:
            cache_dir = Path(cache_dir)
            current = getattr(instance, "cache_dir", None)
            if current is None or cache_dir != current:
                instance.cache_dir = cache_dir
                instance.dag_file = instance.cache_dir / "global_dag.json"
                logger.info(
                    "Reconfigured GlobalDAG cache directory: %s", instance.dag_file
                )
                try:
                    instance.reload_from_disk()
                except STATE_ERRORS as e:
                    logger.error("Failed to reload GlobalDAG after reconfigure: %s", e)

        return instance

    def _lock_path(self) -> Path:
        """Return path to the lock file used for cross-process coordination."""
        # Use a lock file next to the DAG JSON, e.g. global_dag.json.lock
        return self.dag_file.with_suffix(self.dag_file.suffix + ".lock")

    @contextmanager
    def _acquire_lock(self, timeout: float = 30.0, poll_interval: float = 0.1):
        """Acquire an exclusive file lock for DAG operations.

        On POSIX platforms this uses fcntl.flock for robustness. On
        platforms without fcntl (e.g. Windows), it falls back to a
        best-effort lock file based on O_CREAT | O_EXCL.

        Args:
            timeout: Maximum time in seconds to wait for the lock when
                using the lock-file fallback.
            poll_interval: Sleep interval between retries.

        Raises:
            TimeoutError: If the lock cannot be acquired within timeout.
        """
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self._lock_path()

        if _HAS_FCNTL:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
        else:  # Fallback for non-posix platforms
            start = time.time()
            fd: Optional[int] = None
            while True:
                try:
                    fd = os.open(
                        str(lock_path),
                        os.O_CREAT | os.O_EXCL | os.O_RDWR,
                    )
                    break
                except FileExistsError as lock_err:
                    if time.time() - start > timeout:
                        logger.error("Timeout acquiring GlobalDAG lock: %s", lock_path)
                        raise TimeoutError(
                            f"Timeout acquiring GlobalDAG lock: {lock_path}"
                        ) from lock_err
                    time.sleep(poll_interval)

            try:
                yield
            finally:
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                try:
                    os.unlink(lock_path)
                except FileNotFoundError:
                    pass
                except OSError as e:
                    logger.debug(
                        "Failed to remove GlobalDAG lock file %s: %s",
                        lock_path,
                        e,
                    )

    def _save_unlocked(self) -> None:
        """Save DAG to disk without acquiring a lock.

        Caller must hold the file lock via _acquire_lock().
        """
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        dag_data = {
            "nodes": list(self._dag.nodes()),
            "edges": list(self._dag.edges()),
        }

        with open(self.dag_file, "w", encoding="utf-8") as f:
            json.dump(dag_data, f, indent=2)

        logger.debug("Saved global DAG to %s", self.dag_file)

    def _save(self) -> None:
        """Save DAG to disk under an exclusive file lock."""
        try:
            with self._acquire_lock():
                self._save_unlocked()
        except IO_ERRORS as e:
            logger.error("Failed to save global DAG: %s", e)

    def _load_unlocked(self) -> None:
        """Load DAG from disk without acquiring a lock.

        Caller must hold the file lock via _acquire_lock().
        """
        # Reset current in-memory DAG before loading from disk so that
        # each load reflects the latest on-disk state.
        self._dag.clear()

        if not self.dag_file.exists():
            logger.debug("No existing global DAG file found at %s", self.dag_file)
            return

        with open(self.dag_file, "r", encoding="utf-8") as f:
            dag_data = json.load(f)

        # Reconstruct DAG from saved data
        nodes = dag_data.get("nodes", [])
        edges = dag_data.get("edges", [])

        self._dag.add_nodes_from(nodes)
        self._dag.add_edges_from(edges)

        logger.info(
            "Loaded global DAG from %s (%d nodes, %d edges)",
            self.dag_file,
            len(nodes),
            len(edges),
        )

    def _load(self) -> None:
        """Load DAG from disk under an exclusive file lock."""
        try:
            with self._acquire_lock():
                self._load_unlocked()
        except STATE_ERRORS as e:
            logger.error("Failed to load global DAG: %s", e)
            # Continue with empty DAG

    def reload_from_disk(self) -> None:
        """Reload DAG contents from disk under the file lock."""
        with self._acquire_lock():
            self._load_unlocked()

    def add_dependency(self, parent_graph_id: str, child_graph_id: str) -> None:
        """Add dependency edge from parent to child transaction.

        Args:
            parent_graph_id: Parent transaction graph ID.
            child_graph_id: Child transaction graph ID (dependency).
        """
        try:
            # Perform a load-merge-save cycle under the file lock so that
            # concurrent writers from multiple processes do not lose edges.
            with self._acquire_lock():
                # Reload latest DAG state from disk before mutating.
                self._load_unlocked()

                if not self._dag.has_node(parent_graph_id):
                    self._dag.add_node(parent_graph_id)
                if not self._dag.has_node(child_graph_id):
                    self._dag.add_node(child_graph_id)

                self._dag.add_edge(parent_graph_id, child_graph_id)
                logger.debug(
                    "Added dependency: %s -> %s", parent_graph_id, child_graph_id
                )

                # Check for cycles and log full cycle path when detected
                if self._check_on_write and not nx.is_directed_acyclic_graph(self._dag):
                    logger.warning(
                        "Cycle detected in global DAG involving %s and %s",
                        parent_graph_id,
                        child_graph_id,
                    )

                    # Attempt to extract and log the complete cycle chain.
                    try:
                        raw_cycle = nx.find_cycle(self._dag, source=parent_graph_id)

                        if raw_cycle:
                            # raw_cycle is a list of edges: (u, v) or (u, v, orientation)
                            cycle_nodes: List[str] = []

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
                            logger.warning("Global DAG cycle path: %s", cycle_repr)
                    except CYCLE_ERRORS as cycle_err:
                        # Cycle introspection is best-effort only and must not break execution.
                        logger.debug(
                            "Failed to compute full cycle path for GlobalDAG: %s",
                            cycle_err,
                        )

                # Persist updated DAG state to disk.
                self._save_unlocked()
        except TimeoutError as lock_err:
            logger.error(
                "Timed out while adding dependency %s -> %s to GlobalDAG: %s",
                parent_graph_id,
                child_graph_id,
                lock_err,
            )
        except GRAPH_ERRORS as e:
            logger.error(
                "Failed to add dependency %s -> %s to GlobalDAG: %s",
                parent_graph_id,
                child_graph_id,
                e,
            )

    def replace_dependencies(
        self, parent_graph_id: str, new_children: Set[str]
    ) -> None:
        """Replace all outgoing dependencies of a parent graph.

        This method removes all existing edges parent_graph_id -> * and then
        adds new edges from parent_graph_id to each graph ID in new_children.
        The entire operation runs inside a single load-modify-save critical
        section protected by the file lock.

        Args:
            parent_graph_id: Graph ID of the parent transaction.
            new_children: Set of child graph IDs that should be the complete
                dependency set for this parent after the operation.
        """
        try:
            with self._acquire_lock():
                # Load latest DAG state.
                self._load_unlocked()

                if not self._dag.has_node(parent_graph_id):
                    self._dag.add_node(parent_graph_id)

                # Remove all existing outgoing edges from parent_graph_id.
                old_successors = list(self._dag.successors(parent_graph_id))
                if old_successors:
                    edges_to_remove = [
                        (parent_graph_id, succ) for succ in old_successors
                    ]
                    self._dag.remove_edges_from(edges_to_remove)

                # Add new dependency edges to each child in new_children.
                for child_id in new_children:
                    if not self._dag.has_node(child_id):
                        self._dag.add_node(child_id)
                    self._dag.add_edge(parent_graph_id, child_id)

                logger.info(
                    "Replaced dependencies for %s: %d previous -> %d new",
                    parent_graph_id,
                    len(old_successors),
                    len(new_children),
                )

                # Check for cycles and log when detected.
                if self._check_on_write and not nx.is_directed_acyclic_graph(self._dag):
                    logger.warning(
                        "Cycle detected in global DAG after replacing dependencies "
                        "for parent %s",
                        parent_graph_id,
                    )
                    try:
                        raw_cycle = nx.find_cycle(self._dag, source=parent_graph_id)
                        if raw_cycle:
                            cycle_nodes: List[str] = []
                            for edge in raw_cycle:
                                u = edge[0]
                                v = edge[1]
                                if not cycle_nodes:
                                    cycle_nodes.append(u)
                                cycle_nodes.append(v)
                            if cycle_nodes and cycle_nodes[0] != cycle_nodes[-1]:
                                cycle_nodes.append(cycle_nodes[0])
                            cycle_repr = " -> ".join(cycle_nodes)
                            logger.warning(
                                "Global DAG cycle path after replace: %s", cycle_repr
                            )
                    except CYCLE_ERRORS as cycle_err:
                        logger.debug(
                            "Failed to compute cycle path after replace_dependencies "
                            "for %s: %s",
                            parent_graph_id,
                            cycle_err,
                        )

                # Persist updated DAG to disk.
                self._save_unlocked()
        except TimeoutError as lock_err:
            logger.error(
                "Timed out while replacing dependencies for %s in GlobalDAG: %s",
                parent_graph_id,
                lock_err,
            )
        except GRAPH_ERRORS as e:
            logger.error(
                "Failed to replace dependencies for %s in GlobalDAG: %s",
                parent_graph_id,
                e,
            )

    def get_dependencies(self, graph_id: str) -> Set[str]:
        """Get direct dependencies of a transaction.

        Args:
            graph_id: Transaction graph ID.

        Returns:
            Set[str]: Set of child graph IDs.
        """
        try:
            with self._acquire_lock():
                self._load_unlocked()

                if not self._dag.has_node(graph_id):
                    return set()
                return set(self._dag.successors(graph_id))
        except GRAPH_ERRORS as e:
            logger.warning(
                "Failed to get dependencies for %s from GlobalDAG: %s", graph_id, e
            )
            return set()

    def get_transitive_dependencies(self, graph_id: str) -> Set[str]:
        """Get all transitive dependencies (direct + indirect) of a transaction.

        Args:
            graph_id: Transaction graph ID.

        Returns:
            Set[str]: Set of all dependent graph IDs (transitive closure).
        """
        try:
            with self._acquire_lock():
                self._load_unlocked()

                if not self._dag.has_node(graph_id):
                    return set()

                # Use NetworkX descendants to get all reachable nodes
                return nx.descendants(self._dag, graph_id)
        except GRAPH_ERRORS as e:
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
        try:
            with self._acquire_lock():
                self._load_unlocked()

                if not self._dag.has_node(graph_id):
                    return set()
                return set(self._dag.predecessors(graph_id))
        except GRAPH_ERRORS as e:
            logger.warning(
                "Failed to get dependents for %s from GlobalDAG: %s", graph_id, e
            )
            return set()

    def topological_order(self) -> List[str]:
        """Get topological ordering of transactions.

        Returns:
            List[str]: Graph IDs in topological order (dependencies first).

        Raises:
            nx.NetworkXError: If graph contains cycles.
        """
        with self._acquire_lock():
            self._load_unlocked()
            try:
                return list(nx.topological_sort(self._dag))
            except nx.NetworkXError as e:
                logger.error("Cannot compute topological order: %s", e)
                raise

    def build_cluster_view(self) -> CondensationResult:
        """Construct a condensation DAG for the global graph.

        The resulting DAG collapses every strongly connected component into a
        synthetic `scc_cluster` node with a `members` attribute listing all
        graph IDs contained in that component. Algorithms that require an
        acyclic input (module grouping, layered governance, etc.) can operate on
        the returned DAG while the original GlobalDAG remains unchanged.

        Returns:
            CondensationResult: Cluster DAG and membership map.
        """

        with self._acquire_lock():
            self._load_unlocked()
            return build_condensation_dag(
                self._dag,
                node_prefix="global_scc:",
                cluster_type=NodeType.SCC_CLUSTER.value,
            )

    def has_cycle(self) -> bool:
        """Check if global DAG contains cycles.

        Returns:
            bool: True if DAG contains cycles.
        """
        with self._acquire_lock():
            self._load_unlocked()
            return not nx.is_directed_acyclic_graph(self._dag)

    def get_all_graph_ids(self) -> Set[str]:
        """Get all transaction graph IDs in DAG.

        Returns:
            Set[str]: Set of all graph IDs.
        """
        with self._acquire_lock():
            self._load_unlocked()
            return set(self._dag.nodes())

    def get_cycles(self, limit: Optional[int] = None) -> List[List[str]]:
        """Enumerate cycles present in the GlobalDAG.

        Each cycle is returned as an ordered list of graph IDs. The starting
        node may appear once (simple_cycles semantics); callers that want a
        closed representation can append the first element at the end.

        Args:
            limit: Optional maximum number of cycles to return. When None or
                a non-positive value, all cycles are returned (may be expensive
                on large graphs).

        Returns:
            List[List[str]]: List of cycles, each represented as a list of
            graph IDs in traversal order.
        """
        cycles: List[List[str]] = []
        try:
            with self._acquire_lock():
                self._load_unlocked()

                max_cycles: Optional[int] = None
                if limit is not None and limit > 0:
                    max_cycles = limit

                try:
                    for cycle in nx.simple_cycles(self._dag):
                        cycles.append([str(node) for node in cycle])
                        if max_cycles is not None and len(cycles) >= max_cycles:
                            break
                except CYCLE_ERRORS as e:
                    logger.warning("Failed to enumerate cycles in GlobalDAG: %s", e)

        except GRAPH_ERRORS as e:
            logger.warning("Failed to get cycles from GlobalDAG: %s", e)

        return cycles

    def clear(self) -> None:
        """Clear DAG (for testing).

        This method clears both the in-memory DAG and the on-disk JSON
        representation under the file lock.
        """
        try:
            with self._acquire_lock():
                self._dag.clear()
                self._save_unlocked()
            logger.debug("Global DAG cleared")
        except GRAPH_ERRORS as e:
            logger.error("Failed to clear GlobalDAG: %s", e)
