"""C/C++ ecosystem dependency fetcher.

Handles fetching of C/C++ dependencies from various sources:
- Git repositories (most common for C++ libraries)
- System package managers (apt, yum, brew) - future support
- Conan/vcpkg - future support
"""

import hashlib
import logging
from pathlib import Path
from typing import Optional

from depanalyzer.parsers.base import BaseDepFetcher, DependencySpec

logger = logging.getLogger("depanalyzer.parsers.cpp.dep_fetcher")


class CppDepFetcher(BaseDepFetcher):
    """Dependency fetcher for C/C++ ecosystem.

    Primarily handles Git-based dependencies common in C++ projects.
    CMake's FetchContent, git submodules, and manual git dependencies
    all typically use Git URLs.
    """

    NAME = "cpp_dep_fetcher"
    ECOSYSTEM = "cpp"

    def can_handle(self, dep_spec: DependencySpec) -> bool:
        """Check if this fetcher can handle the dependency.

        Args:
            dep_spec: Dependency specification.

        Returns:
            bool: True if ecosystem is 'cpp' or source_url is a git URL.
        """
        # Handle if explicitly marked as cpp
        if dep_spec.ecosystem == self.ECOSYSTEM:
            return True

        # Also handle if it looks like a git URL
        if dep_spec.source_url:
            url_lower = dep_spec.source_url.lower()
            if (
                url_lower.endswith(".git")
                or "github.com" in url_lower
                or "gitlab" in url_lower
            ):
                return True

        return False

    def fetch(self, dep_spec: DependencySpec) -> Optional[Path]:
        """Fetch a C/C++ dependency.

        Args:
            dep_spec: Dependency specification.

        Returns:
            Optional[Path]: Local path to fetched dependency, None if failed.
        """
        logger.info("Fetching C++ dependency: %s", dep_spec.name)

        # Get cache directory
        cache_dir = self.get_cache_dir(dep_spec)

        # Case 1: Git URL
        if dep_spec.source_url:
            return self._fetch_from_git(dep_spec, cache_dir)

        # Case 2: Known C++ library (future: query from package manager)
        # For now, we don't support non-git dependencies
        logger.warning(
            "C++ dependency '%s' has no source_url, cannot fetch", dep_spec.name
        )
        return None

    def _fetch_from_git(
        self, dep_spec: DependencySpec, cache_dir: Path
    ) -> Optional[Path]:
        """Fetch dependency from Git repository.

        Args:
            dep_spec: Dependency specification.
            cache_dir: Cache directory.

        Returns:
            Optional[Path]: Local path if successful, None otherwise.
        """
        url = dep_spec.source_url
        if not url:
            return None

        logger.info("Fetching from Git: %s", url)

        # Determine branch/tag
        branch = dep_spec.metadata.get("branch")
        tag = dep_spec.metadata.get("tag") or dep_spec.version

        # Clone repository
        success = self.git_clone(
            url=url,
            target_dir=cache_dir,
            branch=branch,
            tag=tag,
            depth=1,  # Shallow clone
            force=False,  # Don't re-clone if exists
        )

        if success:
            logger.info("Successfully fetched %s to %s", dep_spec.name, cache_dir)
            return cache_dir
        else:
            logger.error("Failed to fetch %s from %s", dep_spec.name, url)
            return None

    def get_cache_dir(self, dep_spec: DependencySpec) -> Path:
        """Get cache directory for C++ dependency.

        Uses format: cpp/<sanitized_name>/<version_or_hash>

        Args:
            dep_spec: Dependency specification.

        Returns:
            Path: Cache directory path.
        """
        # Sanitize name (remove special chars)
        safe_name = dep_spec.name.replace("/", "_").replace(":", "_")

        # Use version if available, otherwise use URL hash
        if dep_spec.version:
            version_dir = dep_spec.version
        elif dep_spec.source_url:
            # Use last part of URL as identifier
            url_hash = hashlib.md5(dep_spec.source_url.encode()).hexdigest()[:8]
            version_dir = f"url_{url_hash}"
        else:
            version_dir = "default"

        return self.cache_root / self.ECOSYSTEM / safe_name / version_dir
