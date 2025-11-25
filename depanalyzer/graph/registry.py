"""Global graph registry for cross-transaction graph management.

Registry maintains mapping of graph_id to disk cache location and summary,
enabling cross-transaction graph queries without loading full graph bodies.

Uses file-based locking for cross-process synchronization instead of
multiprocessing.Manager to avoid orphaned server processes during shutdown.
"""

import json
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, Optional

try:
    import fcntl

    _HAS_FCNTL = True
except ImportError:
    fcntl = None
    _HAS_FCNTL = False

logger = logging.getLogger("depanalyzer.graph.registry")


class GraphRegistry:
    """Global registry for graph snapshots and summaries.

    Maps graph_id to disk cache location and metadata, enabling
    parent transactions to reference child graphs without loading them.

    Uses file-based locking for cross-process synchronization.
    """

    _instance: Optional["GraphRegistry"] = None
    _initialized: bool = False

    def __new__(cls, cache_root: Optional[Path] = None) -> "GraphRegistry":
        """Singleton pattern for global registry.

        Args:
            cache_root: Optional cache root directory (ignored, passed to __init__).

        Returns:
            GraphRegistry: Singleton instance.
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, cache_root: Optional[Path] = None) -> None:
        """Initialize registry.

        Args:
            cache_root: Optional cache root directory.
        """
        if GraphRegistry._initialized:
            return

        self.cache_root = cache_root or Path(".depanalyzer_cache/graphs")
        self.cache_root.mkdir(parents=True, exist_ok=True)

        self._registry_file = self.cache_root / "registry.json"
        # Use regular dict instead of Manager.dict() - file locking handles cross-process sync
        self._registry: Dict[str, Dict[str, Any]] = {}
        self._load_registry()

        GraphRegistry._initialized = True
        logger.info(
            "Graph registry initialized at: %s (PID: %d)", self.cache_root, os.getpid()
        )

    @classmethod
    def get_instance(cls, cache_root: Optional[Path] = None) -> "GraphRegistry":
        """Get the singleton GraphRegistry instance.

        Args:
            cache_root: Optional cache root directory (only used on first initialization).

        Returns:
            GraphRegistry: The singleton instance.
        """
        if cls._instance is None:
            cls(cache_root=cache_root)
            return cls._instance

        instance: "GraphRegistry" = cls._instance

        # Allow callers to rebind the cache_root when a different graphs root
        # is specified (for example via CLI --cache-dir). This avoids silently
        # operating on whichever cache_root happened to be used first.
        if cache_root is not None:
            cache_root = Path(cache_root)
            current = getattr(instance, "cache_root", None)
            if current is None or cache_root != current:
                instance.configure_cache_root(cache_root)
                logger.info(
                    "Reconfigured GraphRegistry cache_root to %s", instance.cache_root
                )

        return instance

    def _lock_path(self) -> Path:
        """Return path to the lock file used for cross-process coordination."""
        return self._registry_file.with_suffix(".lock")

    @contextmanager
    def _acquire_lock(
        self, timeout: float = 30.0, poll_interval: float = 0.1
    ) -> Generator[None, None, None]:
        """Acquire an exclusive file lock for registry operations.

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
        self.cache_root.mkdir(parents=True, exist_ok=True)
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
        else:  # Fallback for non-posix platforms (Windows)
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
                        logger.error("Timeout acquiring registry lock: %s", lock_path)
                        raise TimeoutError(
                            f"Timeout acquiring registry lock: {lock_path}"
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
                        "Failed to remove registry lock file %s: %s",
                        lock_path,
                        e,
                    )

    def _load_registry(self) -> None:
        """Load registry from disk with file locking."""
        try:
            with self._acquire_lock():
                self._load_registry_unlocked()
        except (OSError, TimeoutError) as e:
            logger.warning("Failed to load registry with lock: %s", e)
            # Fallback to unlocked load
            self._load_registry_unlocked()

    def configure_cache_root(self, cache_root: Path) -> None:
        """Configure registry to use a new cache root and reload state."""
        resolved_root = Path(cache_root)
        resolved_root.mkdir(parents=True, exist_ok=True)
        self.cache_root = resolved_root
        self._registry_file = self.cache_root / "registry.json"
        self._load_registry_unlocked()

    def _load_registry_unlocked(self) -> None:
        """Load registry from disk without acquiring the lock."""
        if not self._registry_file.exists():
            return

        try:
            with open(self._registry_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Replace current contents with on-disk data
            self._registry.clear()
            self._registry.update(data)
            logger.info("Loaded registry with %d entries", len(self._registry))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load registry: %s", e)

    def _save_registry_unlocked(self) -> None:
        """Save registry to disk without acquiring lock (internal use only).

        Caller must hold the lock before calling this method.
        """
        try:
            # Convert manager dict to regular dict for JSON serialization
            registry_data = dict(self._registry)
            with open(self._registry_file, "w", encoding="utf-8") as f:
                json.dump(registry_data, f, indent=2, ensure_ascii=False)
            logger.debug("Registry saved with %d entries", len(self._registry))
        except OSError as e:
            logger.error("Failed to save registry: %s", e)

    def _save_registry(self) -> None:
        """Save registry to disk with file locking for multi-process safety."""
        try:
            with self._acquire_lock():
                self._save_registry_unlocked()
        except (OSError, TimeoutError) as e:
            logger.error("Failed to save registry with lock: %s", e)

    def register(
        self,
        graph_id: str,
        cache_path: Path,
        summary: Dict[str, Any],
    ) -> None:
        """Register a graph with its cache location and summary.

        Args:
            graph_id: Unique graph identifier.
            cache_path: Path to graph cache file.
            summary: Graph summary (node counts, artifacts, etc.).
        """
        try:
            with self._acquire_lock():
                # Reload to get latest state from other processes
                self._load_registry_unlocked()
                self._registry[graph_id] = {
                    "cache_path": str(cache_path),
                    "summary": summary,
                }
                self._save_registry_unlocked()
                logger.info("Registered graph: %s (PID: %d)", graph_id, os.getpid())
        except (OSError, TimeoutError) as e:
            logger.error("Failed to register graph %s: %s", graph_id, e)

    def get_entry(self, graph_id: str) -> Optional[Dict[str, Any]]:
        """Get registry entry for a graph.

        Args:
            graph_id: Graph identifier.

        Returns:
            Optional[Dict[str, Any]]: Registry entry or None if not found.
        """
        try:
            with self._acquire_lock():
                self._load_registry_unlocked()
                return self._registry.get(graph_id)
        except (OSError, TimeoutError) as e:
            logger.warning("Failed to get entry for %s: %s", graph_id, e)
            return self._registry.get(graph_id)

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
        try:
            with self._acquire_lock():
                self._load_registry_unlocked()
                return graph_id in self._registry
        except (OSError, TimeoutError) as e:
            logger.warning("Failed to check graph %s: %s", graph_id, e)
            return graph_id in self._registry

    def list_graphs(self) -> list[str]:
        """List all registered graph IDs.

        Returns:
            list[str]: List of graph IDs.
        """
        try:
            with self._acquire_lock():
                self._load_registry_unlocked()
                return list(self._registry.keys())
        except (OSError, TimeoutError) as e:
            logger.warning("Failed to list graphs: %s", e)
            return list(self._registry.keys())

    def clear(self) -> None:
        """Clear registry (for testing)."""
        try:
            with self._acquire_lock():
                self._registry.clear()
                self._save_registry_unlocked()
                logger.warning("Registry cleared")
        except (OSError, TimeoutError) as e:
            logger.error("Failed to clear registry: %s", e)

    @classmethod
    def shutdown(cls) -> None:
        """Reset singleton state (call on application exit).

        No Manager to shutdown - just resets singleton state.
        """
        logger.info("GraphRegistry shutdown complete")
        cls._instance = None
        cls._initialized = False
