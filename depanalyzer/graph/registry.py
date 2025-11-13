"""Global graph registry for cross-transaction graph management.

Registry maintains mapping of graph_id to disk cache location and summary,
enabling cross-transaction graph queries without loading full graph bodies.

Supports multi-process access using multiprocessing.Manager for shared state.
"""

import json
import logging
import multiprocessing
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("depanalyzer.graph.registry")


class GraphRegistry:
    """Global registry for graph snapshots and summaries.

    Maps graph_id to disk cache location and metadata, enabling
    parent transactions to reference child graphs without loading them.

    Uses multiprocessing.Manager for shared state across processes.
    """

    _instance: Optional["GraphRegistry"] = None
    _manager: Optional[multiprocessing.managers.SyncManager] = None
    _lock: Optional[multiprocessing.Lock] = None
    _initialized: bool = False

    def __new__(cls) -> "GraphRegistry":
        """Singleton pattern for global registry.

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

        # Initialize manager for shared state
        if GraphRegistry._manager is None:
            GraphRegistry._manager = multiprocessing.Manager()
            GraphRegistry._lock = GraphRegistry._manager.Lock()
            logger.info("Initialized multiprocessing Manager for shared state")

        self.cache_root = cache_root or Path(".depanalyzer_cache/graphs")
        self.cache_root.mkdir(parents=True, exist_ok=True)

        self._registry_file = self.cache_root / "registry.json"
        # Use manager dict for cross-process sharing
        self._registry: Dict[str, Dict[str, Any]] = GraphRegistry._manager.dict()
        self._load_registry()

        GraphRegistry._initialized = True
        logger.info(
            "Graph registry initialized at: %s (PID: %d)", self.cache_root, os.getpid()
        )

    def _load_registry(self) -> None:
        """Load registry from disk."""
        if self._registry_file.exists():
            try:
                with open(self._registry_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Load into manager dict
                with GraphRegistry._lock:
                    self._registry.update(data)
                logger.info("Loaded registry with %d entries", len(self._registry))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load registry: %s", e)

    def _save_registry(self) -> None:
        """Save registry to disk with file locking for multi-process safety."""
        try:
            with GraphRegistry._lock:
                # Convert manager dict to regular dict for JSON serialization
                registry_data = dict(self._registry)
                with open(self._registry_file, "w", encoding="utf-8") as f:
                    json.dump(registry_data, f, indent=2, ensure_ascii=False)
            logger.debug("Registry saved with %d entries", len(self._registry))
        except OSError as e:
            logger.error("Failed to save registry: %s", e)

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
        with GraphRegistry._lock:
            self._registry[graph_id] = {
                "cache_path": str(cache_path),
                "summary": summary,
            }
            self._save_registry()
            logger.info("Registered graph: %s (PID: %d)", graph_id, os.getpid())

    def get_entry(self, graph_id: str) -> Optional[Dict[str, Any]]:
        """Get registry entry for a graph.

        Args:
            graph_id: Graph identifier.

        Returns:
            Optional[Dict[str, Any]]: Registry entry or None if not found.
        """
        with GraphRegistry._lock:
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
        with GraphRegistry._lock:
            return graph_id in self._registry

    def list_graphs(self) -> list[str]:
        """List all registered graph IDs.

        Returns:
            list[str]: List of graph IDs.
        """
        with GraphRegistry._lock:
            return list(self._registry.keys())

    def clear(self) -> None:
        """Clear registry (for testing)."""
        with GraphRegistry._lock:
            self._registry.clear()
            self._save_registry()
            logger.warning("Registry cleared")

    @classmethod
    def shutdown(cls) -> None:
        """Shutdown the manager (call on application exit)."""
        if cls._manager is not None:
            logger.info("Shutting down GraphRegistry manager")
            cls._manager.shutdown()
            cls._manager = None
            cls._lock = None
            cls._instance = None
            cls._initialized = False
