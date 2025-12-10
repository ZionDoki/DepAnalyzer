"""Dependency fetch pool for FETCH_DEPENDENCY tasks.

This module provides DependencyFetchPool, a specialized pool for network I/O
dependency fetching tasks.

Key features:
- Optimized for network I/O operations
- Convenience methods for dependency submission
- Real-time result streaming via ResultIterator
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from depanalyzer.runtime.pools.process_pool import ProcessTaskPool
from depanalyzer.runtime.task_types import Task, TaskType, create_task


class DependencyFetchPool(ProcessTaskPool):
    """Specialized pool for FETCH_DEPENDENCY tasks.

    This pool is optimized for network I/O dependency fetching operations.
    It uses ProcessPoolExecutor to parallelize network requests.

    Usage:
        pool = DependencyFetchPool(max_workers=8)
        pool.start()

        for dep in dependencies:
            pool.submit_dependency(dep.name, dep.version, ecosystem="npm")
        pool.seal()

        for result in pool.results():
            if result.success:
                process_fetched_dependency(result.result)
            else:
                handle_fetch_error(result.error)

    Attributes:
        max_workers: Maximum number of worker processes.
        cache_root: Root directory for dependency cache.
    """

    def __init__(
        self,
        max_workers: Optional[int] = None,
        cache_root: Optional[Path] = None,
    ) -> None:
        """Initialize the dependency fetch pool.

        Args:
            max_workers: Maximum number of worker processes.
                Defaults to CPU count (network I/O can benefit from more workers).
            cache_root: Root directory for dependency cache.
                Defaults to ".dep_cache".
        """
        workers = max_workers or os.cpu_count() or 4
        super().__init__(workers, name="DependencyFetchPool")
        self._cache_root = cache_root or Path(".dep_cache")

    def submit_dependency(
        self,
        name: str,
        version: Optional[str],
        ecosystem: str,
        source_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timeout: float = 120.0,
    ) -> str:
        """Submit a dependency for fetching.

        Convenience method that creates a FETCH_DEPENDENCY task and submits it.

        Args:
            name: Dependency name.
            version: Version string (optional).
            ecosystem: Ecosystem identifier (e.g., "npm", "maven", "pip").
            source_url: Optional source URL for the dependency.
            metadata: Optional metadata dictionary.
            timeout: Task timeout in seconds.

        Returns:
            Task ID for tracking.

        Raises:
            RuntimeError: If the pool is sealed.
        """
        task = create_task(
            TaskType.FETCH_DEPENDENCY,
            payload={
                "name": name,
                "version": version,
                "ecosystem": ecosystem,
                "source_url": source_url,
                "metadata": metadata or {},
                "cache_root": str(self._cache_root),
            },
            timeout=timeout,
        )
        self.submit(task)
        return task.task_id

    def submit_dependencies(
        self,
        dependencies: list,
        ecosystem: str,
        timeout: float = 120.0,
    ) -> list:
        """Submit multiple dependencies for fetching.

        Convenience method for batch dependency submission.

        Args:
            dependencies: List of dependency specs. Each spec should be a dict
                with at least 'name' key, and optionally 'version', 'source_url',
                and 'metadata' keys.
            ecosystem: Ecosystem identifier.
            timeout: Task timeout in seconds.

        Returns:
            List of task IDs for tracking.

        Raises:
            RuntimeError: If the pool is sealed.
        """
        task_ids = []
        for dep in dependencies:
            if isinstance(dep, dict):
                task_id = self.submit_dependency(
                    name=dep.get("name"),
                    version=dep.get("version"),
                    ecosystem=ecosystem,
                    source_url=dep.get("source_url"),
                    metadata=dep.get("metadata"),
                    timeout=timeout,
                )
            else:
                # Assume it's a DependencySpec-like object
                task_id = self.submit_dependency(
                    name=getattr(dep, "name", str(dep)),
                    version=getattr(dep, "version", None),
                    ecosystem=ecosystem,
                    source_url=getattr(dep, "source_url", None),
                    metadata=getattr(dep, "metadata", None),
                    timeout=timeout,
                )
            task_ids.append(task_id)
        return task_ids
