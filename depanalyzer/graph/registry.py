"""Global graph registry for cross-transaction graph management.

Registry maintains mapping of graph_id to disk cache location and summary,
enabling cross-transaction graph queries without loading full graph bodies.
"""

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("depanalyzer.graph.registry")


class GraphRegistry:
    """Global registry for graph snapshots and summaries.

    Maps graph_id to disk cache location and metadata, enabling
    parent transactions to reference child graphs without loading them.
    """

    _instance: Optional["GraphRegistry"] = None
    _lock = threading.RLock()

    def __new__(cls) -> "GraphRegistry":
        """Singleton pattern for global registry.

        Returns:
            GraphRegistry: Singleton instance.
        """
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, cache_root: Optional[Path] = None) -> None:
        """Initialize registry.

        Args:
            cache_root: Optional cache root directory.
        """
        if self._initialized:
            return

        self.cache_root = cache_root or Path(".depanalyzer_cache/graphs")
        self.cache_root.mkdir(parents=True, exist_ok=True)

        self._registry_file = self.cache_root / "registry.json"
        self._registry: Dict[str, Dict[str, Any]] = {}
        self._load_registry()

        self._initialized = True
        logger.info("Graph registry initialized at: %s", self.cache_root)

    def _load_registry(self) -> None:
        """Load registry from disk."""
        if self._registry_file.exists():
            try:
                with open(self._registry_file, "r", encoding="utf-8") as f:
                    self._registry = json.load(f)
                logger.info("Loaded registry with %d entries", len(self._registry))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load registry: %s", e)
                self._registry = {}

    def _save_registry(self) -> None:
        """Save registry to disk."""
        try:
            with self._lock:
                with open(self._registry_file, "w", encoding="utf-8") as f:
                    json.dump(self._registry, f, indent=2, ensure_ascii=False)
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
        with self._lock:
            self._registry[graph_id] = {
                "cache_path": str(cache_path),
                "summary": summary,
            }
            self._save_registry()
            logger.info("Registered graph: %s", graph_id)

    def get_entry(self, graph_id: str) -> Optional[Dict[str, Any]]:
        """Get registry entry for a graph.

        Args:
            graph_id: Graph identifier.

        Returns:
            Optional[Dict[str, Any]]: Registry entry or None if not found.
        """
        with self._lock:
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
        with self._lock:
            return graph_id in self._registry

    def list_graphs(self) -> list[str]:
        """List all registered graph IDs.

        Returns:
            list[str]: List of graph IDs.
        """
        with self._lock:
            return list(self._registry.keys())

    def clear(self) -> None:
        """Clear registry (for testing)."""
        with self._lock:
            self._registry.clear()
            self._save_registry()
            logger.warning("Registry cleared")
