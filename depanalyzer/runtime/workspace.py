"""Workspace management for source code acquisition and caching.

Handles local paths and Git URLs, providing a clean read-only view
for detectors and parsers.
"""

import hashlib
import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("depanalyzer.workspace")


class Workspace:
    """Workspace manager for source code acquisition.

    Provides unified management for local paths and Git repositories,
    with caching support for remote sources.
    """

    def __init__(self, source: str, cache_root: Optional[Path] = None) -> None:
        """Initialize workspace from local path or Git URL.

        Args:
            source: Local path or Git URL.
            cache_root: Optional cache directory for Git clones.
        """
        self.source = source
        self.cache_root = cache_root or Path(".depanalyzer_cache/workspaces")
        self._root_path: Optional[Path] = None
        self._is_git = self._detect_git_url(source)

        logger.info("Workspace initialized for source: %s", source)

    @staticmethod
    def _detect_git_url(source: str) -> bool:
        """Detect if source is a Git URL.

        Args:
            source: Source string.

        Returns:
            bool: True if source is a Git URL.
        """
        git_patterns = ["git@", "https://", "http://", "ssh://", ".git"]
        return any(pattern in source for pattern in git_patterns)

    def _compute_cache_path(self) -> Path:
        """Compute stable cache path for Git URL.

        Returns:
            Path: Cache directory path.
        """
        url_hash = hashlib.sha256(self.source.encode()).hexdigest()[:12]
        return self.cache_root / url_hash

    def acquire(self) -> Path:
        """Acquire source code and return root path.

        For local paths, returns the path directly.
        For Git URLs, clones to cache and returns cache path.

        Returns:
            Path: Root path for analysis.
        """
        if self._root_path:
            return self._root_path

        if not self._is_git:
            # Local path
            self._root_path = Path(self.source).resolve()
            if not self._root_path.exists():
                raise FileNotFoundError(f"Local path does not exist: {self.source}")
            logger.info("Using local workspace: %s", self._root_path)
            return self._root_path

        # Git URL - clone to cache
        cache_path = self._compute_cache_path()
        self.cache_root.mkdir(parents=True, exist_ok=True)

        if cache_path.exists():
            logger.info("Using cached workspace: %s", cache_path)
            self._root_path = cache_path
            return self._root_path

        logger.info("Cloning Git repository: %s", self.source)
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", self.source, str(cache_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info("Cloned to cache: %s", cache_path)
            self._root_path = cache_path
            return self._root_path
        except subprocess.CalledProcessError as e:
            logger.error("Git clone failed: %s", e.stderr)
            raise RuntimeError(f"Failed to clone repository: {self.source}") from e

    def get_root(self) -> Path:
        """Get workspace root path.

        Returns:
            Path: Root path.

        Raises:
            RuntimeError: If workspace not acquired yet.
        """
        if not self._root_path:
            raise RuntimeError("Workspace not acquired. Call acquire() first.")
        return self._root_path

    def get_signature(self) -> str:
        """Compute stable signature for this workspace.

        Returns:
            str: Workspace signature.
        """
        if self._is_git:
            return hashlib.sha256(self.source.encode()).hexdigest()[:16]
        return hashlib.sha256(str(self._root_path).encode()).hexdigest()[:16]
