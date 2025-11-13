"""Git repository fetcher.

Simple fetcher for Git-based dependencies.
Performs shallow clone (depth=1) for efficiency.
"""

import logging
import subprocess
from pathlib import Path

from core.dependency import DependencyResult, DependencySpec, DependencyType
from parsers.deps.fetchers.base import BaseFetcher

logger = logging.getLogger("depanalyzer.fetchers.git")


class GitFetcher(BaseFetcher):
    """Fetcher for Git-based dependencies.

    Clones Git repositories specified by source_url.
    Uses shallow clone (--depth 1) for efficiency.
    """

    supports_types = (DependencyType.GIT,)

    def fetch(self, spec: DependencySpec, cache_root: Path) -> DependencyResult:
        """Clone a Git repository to local cache.

        Args:
            spec: Dependency specification (must have source_url).
            cache_root: Root directory for caching dependencies.

        Returns:
            DependencyResult: Fetch result with local_path on success.
        """
        cache_path = self.get_cache_dir(spec, cache_root)
        self.ensure_dir(cache_path.parent)

        if not spec.source_url:
            logger.error("Git dependency %s missing source_url", spec.name)
            return DependencyResult(
                spec=spec, success=False, error=ValueError("Git URL required")
            )

        # Shallow clone for efficiency
        cmd = ["git", "clone", "--depth", "1", spec.source_url, str(cache_path)]
        logger.debug("Cloning Git repository: %s", " ".join(cmd))
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)

        if res.returncode != 0:
            logger.error("Git clone failed for %s: %s", spec.name, res.stderr)
            return DependencyResult(
                spec=spec, success=False, error=RuntimeError(res.stderr)
            )

        logger.info("Successfully cloned %s to %s", spec.name, cache_path)
        return DependencyResult(spec=spec, success=True, local_path=cache_path)
