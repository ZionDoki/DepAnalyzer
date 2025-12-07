"""Workspace management for source code acquisition and caching.

Handles local paths and Git URLs, providing a clean read-only view
for detectors and parsers.
"""

# Workspace utilities guard subprocess and filesystem failures for resilience.


import hashlib
import logging
import re
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger("depanalyzer.workspace")

# Allowed protocols for Git URLs
_ALLOWED_GIT_PROTOCOLS = {"https", "http", "ssh", "git"}

# Pattern for SSH-style Git URLs (git@host:path)
_GIT_SSH_PATTERN = re.compile(r"^git@[\w.-]+:[\w./_-]+(?:\.git)?$")

# Pattern for standard Git URLs
_GIT_URL_PATTERN = re.compile(
    r"^(https?|ssh|git)://[\w.-]+(?::\d+)?/[\w./_-]+(?:\.git)?$"
)


class GitURLValidationError(ValueError):
    """Raised when a Git URL fails validation."""

    pass


def validate_git_url(url: str) -> bool:
    """Validate that a Git URL is safe to clone.

    Args:
        url: The URL to validate.

    Returns:
        bool: True if the URL is valid and safe.

    Raises:
        GitURLValidationError: If the URL is invalid or potentially unsafe.
    """
    # Check for SSH-style URLs (git@host:path)
    if _GIT_SSH_PATTERN.match(url):
        return True

    # Check for standard URLs
    if _GIT_URL_PATTERN.match(url):
        parsed = urlparse(url)
        if parsed.scheme in _ALLOWED_GIT_PROTOCOLS:
            return True

    # Check if it looks like a URL but with disallowed protocol
    parsed = urlparse(url)
    if parsed.scheme:
        if parsed.scheme not in _ALLOWED_GIT_PROTOCOLS:
            raise GitURLValidationError(
                f"Disallowed protocol '{parsed.scheme}' in Git URL: {url}. "
                f"Allowed protocols: {', '.join(sorted(_ALLOWED_GIT_PROTOCOLS))}"
            )

    # If it contains suspicious characters, reject it
    suspicious_chars = [";", "|", "&", "$", "`", "(", ")", "{", "}", "<", ">", "\n", "\r"]
    for char in suspicious_chars:
        if char in url:
            raise GitURLValidationError(
                f"Suspicious character '{char}' in Git URL: {url}"
            )

    # If we get here and it was detected as a Git URL, it's likely malformed
    raise GitURLValidationError(f"Invalid Git URL format: {url}")


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

        # Git URL - validate and clone to cache
        # Validate URL before cloning to prevent command injection
        try:
            validate_git_url(self.source)
        except GitURLValidationError as e:
            logger.error("Invalid Git URL: %s", e)
            raise

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
                timeout=300,  # 5 minute timeout to prevent indefinite hangs
            )
            logger.info("Cloned to cache: %s", cache_path)
            self._root_path = cache_path
            return self._root_path
        except subprocess.TimeoutExpired as e:
            logger.error("Git clone timed out after 300s: %s", self.source)
            raise RuntimeError(f"Git clone timed out for: {self.source}") from e
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

    @property
    def root_path(self) -> Path:
        """Workspace root path property.

        Returns:
            Path: Root path.

        Raises:
            RuntimeError: If workspace not acquired yet.
        """
        if self._root_path is None:
            raise RuntimeError("Workspace not acquired. Call acquire() first.")
        return self._root_path

    def get_signature(self) -> str:
        """Compute stable signature for this workspace.

        Returns:
            str: Workspace signature.

        Raises:
            RuntimeError: If workspace not acquired yet (for local workspaces).
        """
        # Git workspaces: signature is derived from the source URL so that
        # the same remote repository yields the same identifier across runs.
        if self._is_git:
            return hashlib.sha256(self.source.encode()).hexdigest()[:16]

        # Local workspaces: signature is derived from the resolved root path.
        # Ensure workspace has been acquired first.
        if self._root_path is None:
            raise RuntimeError("Workspace not acquired. Call acquire() first.")
        return hashlib.sha256(str(self._root_path).encode()).hexdigest()[:16]

    def get_revision(self) -> Optional[str]:
        """Return the current revision identifier for this workspace.

        For Git-based workspaces, this is the HEAD commit SHA of the cloned
        repository. For plain local directories, the revision is unknown and
        this method returns None.

        Returns:
            Optional[str]: Revision identifier (e.g. Git commit SHA) or None
            when no meaningful revision can be determined.
        """
        if not self._is_git:
            # Non-Git workspaces do not have a well-defined revision.
            return None

        # Ensure the workspace has been acquired so that the clone exists.
        root_path = self.get_root()

        try:
            result = subprocess.run(
                ["git", "-C", str(root_path), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            )
            revision = result.stdout.strip()
            if revision:
                return revision
        except subprocess.CalledProcessError as e:
            logger.warning(
                "Failed to determine Git revision for workspace %s: %s",
                root_path,
                e.stderr,
            )
        # Remove broad exception catch; rely on CalledProcessError above.

        return None
