"""Base fetcher interface for dependency resolution.

Defines abstract interface for ecosystem-specific fetchers (Git, Hvigor, npm, etc.).
"""

from abc import ABC, abstractmethod
from pathlib import Path

from core.dependency import DependencyResult, DependencySpec, DependencyType


class BaseFetcher(ABC):
    """Abstract fetcher interface for different package ecosystems.

    Each fetcher is responsible for downloading/cloning dependencies
    from a specific ecosystem (e.g., Git, OHPM, npm) to local cache.

    Conventions:
    - `fetch` returns a `DependencyResult` with metadata and local path
    - When supporting multiple types, override `supports_types`
    - Fetchers are stateless and can be reused across multiple specs
    """

    supports_types: tuple[DependencyType, ...] = tuple()

    def supports(self, spec: DependencySpec) -> bool:
        """Check if this fetcher supports the given dependency type.

        Args:
            spec: Dependency specification.

        Returns:
            bool: True if this fetcher can handle the dependency type.
        """
        return spec.dependency_type in self.supports_types

    @abstractmethod
    def fetch(self, spec: DependencySpec, cache_root: Path) -> DependencyResult:
        """Fetch the dependency to local cache.

        Args:
            spec: Dependency specification to fetch.
            cache_root: Root directory for caching dependencies.

        Returns:
            DependencyResult: Result of the fetch operation.
        """
        raise NotImplementedError

    def get_cache_dir(self, spec: DependencySpec, cache_root: Path) -> Path:
        """Get cache directory path for a dependency spec.

        Args:
            spec: Dependency specification.
            cache_root: Root cache directory.

        Returns:
            Path: Cache directory for this specific dependency.
        """
        return cache_root / spec.cache_path

    @staticmethod
    def ensure_dir(path: Path) -> None:
        """Create directory if it doesn't exist.

        Args:
            path: Directory path to create.
        """
        path.mkdir(parents=True, exist_ok=True)
