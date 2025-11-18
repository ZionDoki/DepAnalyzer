"""Hvigor ecosystem dependency fetcher.

Handles fetching of HarmonyOS dependencies from:
- OHPM (OpenHarmony Package Manager) registry
- Git repositories (for open-source HAR/HSP libraries)

Special features:
- Time-based version resolution: finds git commit closest to release time
- License-only mode: handles packages without source repositories
- Full clone for accurate version matching
"""

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

logger = logging.getLogger("depanalyzer.parsers.hvigor.dep_fetcher")

# Optional requests library
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    logger.warning("requests library not available, OHPM registry fetching will be limited")

# Optional semantic version sorter
try:
    from packaging.version import Version as PKGVERSION
    HAS_PACKAGING = True
except ImportError:
    PKGVERSION = None
    HAS_PACKAGING = False
    logger.debug("packaging library not available, will use lexical version sorting")

from depanalyzer.parsers.base import BaseDepFetcher, DependencySpec


class HvigorDepFetcher(BaseDepFetcher):
    """Dependency fetcher for Hvigor/HarmonyOS ecosystem.

    Handles OHPM packages, Git-based HAR/HSP libraries, and local dependencies.

    Implements time-based commit selection for accurate version matching with
    OHPM registry releases.
    """

    NAME = "hvigor_dep_fetcher"
    ECOSYSTEM = "hvigor"
    REGISTRY_BASE = "https://ohpm.openharmony.cn/ohpm"

    def can_handle(self, dep_spec: DependencySpec) -> bool:
        """Check if this fetcher can handle the dependency.

        Args:
            dep_spec: Dependency specification.

        Returns:
            bool: True if ecosystem is 'hvigor' or 'harmonyos'.
        """
        return dep_spec.ecosystem in (self.ECOSYSTEM, "harmonyos", "ohpm")

    def fetch(self, dep_spec: DependencySpec) -> Optional[Path]:
        """Fetch a Hvigor dependency.

        Args:
            dep_spec: Dependency specification.

        Returns:
            Optional[Path]: Local path to fetched dependency, None if failed.
        """
        logger.info("Fetching Hvigor dependency: %s", dep_spec.name)

        # Determine fetch method based on metadata
        dep_type = dep_spec.metadata.get("type", "ohpm")

        # Local dependencies are always resolved via explicit paths and do not
        # participate in versioned caching.
        if dep_type == "local":
            return self._handle_local_dependency(dep_spec)

        # Git-based dependencies use the generic cache-dir layout which
        # includes the requested version string. This is safe because Git
        # repositories already encode their own versioning scheme (tags/refs).
        if dep_type == "git" or (dep_spec.source_url and ".git" in dep_spec.source_url):
            cache_dir = self.get_cache_dir(dep_spec)

            if cache_dir.exists() and self._is_valid_cache(cache_dir):
                logger.info("Using cached Git dependency: %s", cache_dir)
                return cache_dir

            return self._fetch_from_git(dep_spec, cache_dir)

        # OHPM registry dependencies are resolved to an exact version first
        # and then cached under hvigor/<safe_name>/<resolved_version> so that
        # different semver ranges which resolve to the same concrete version
        # reuse the same on-disk checkout.
        if dep_type == "ohpm":
            return self._fetch_from_ohpm(dep_spec)

        logger.warning("Unknown Hvigor dependency type: %s", dep_type)
        return None

    def _is_valid_cache(self, cache_dir: Path) -> bool:
        """Check if cached directory is valid.

        Args:
            cache_dir: Cache directory to check.

        Returns:
            bool: True if cache is valid.
        """
        # Check for metadata file or git directory
        return (cache_dir / ".hvigor_meta.json").exists() or (cache_dir / ".git").exists()

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
            logger.error("No source_url provided for Git dependency: %s", dep_spec.name)
            return None

        logger.info("Fetching Hvigor dependency from Git: %s", url)

        # Determine branch/tag
        branch = dep_spec.metadata.get("branch")
        tag = dep_spec.metadata.get("tag") or dep_spec.version

        # Clone repository
        success = self.git_clone(
            url=url,
            target_dir=cache_dir,
            branch=branch,
            tag=tag,
            depth=1,
            force=False,
        )

        if success:
            logger.info("Successfully fetched %s to %s", dep_spec.name, cache_dir)
            return cache_dir
        else:
            logger.error("Failed to fetch %s from %s", dep_spec.name, url)
            return None

    def _fetch_from_ohpm(self, dep_spec: DependencySpec) -> Optional[Path]:
        """Fetch dependency from OHPM registry.

        Args:
            dep_spec: Dependency specification.

        Returns:
            Optional[Path]: Local path if successful, None otherwise.
        """
        if not HAS_REQUESTS:
            logger.error("requests library required for OHPM fetching")
            return None

        logger.info("Fetching from OHPM registry: %s@%s", dep_spec.name, dep_spec.version)

        # Fetch metadata from registry
        try:
            meta = self._fetch_ohpm_metadata(dep_spec.name)
        except Exception as e:
            logger.error("Failed to fetch OHPM metadata for %s: %s", dep_spec.name, e)
            return None

        # Resolve version (may be a semver range or tag). The resolved version
        # is used as the canonical cache key so that multiple range expressions
        # resolving to the same concrete version share a single checkout.
        try:
            resolved_version = self._resolve_version(meta, dep_spec.version)
        except ValueError as e:
            logger.error("Failed to resolve version for %s: %s", dep_spec.name, e)
            return None

        logger.info("Resolved version: %s -> %s", dep_spec.version, resolved_version)

        # Derive cache directory based on resolved version so that identical
        # resolved versions reuse the same cache directory even when requested
        # via different semver ranges.
        cache_dir = self._get_ohpm_cache_dir(dep_spec.name, resolved_version)

        # If we already have a valid cache for the resolved version, reuse it.
        if cache_dir.exists() and self._is_valid_cache(cache_dir):
            logger.info(
                "Using cached OHPM dependency: %s (resolved_version=%s)",
                cache_dir,
                resolved_version,
            )
            return cache_dir

        # Get version info
        vinfo = meta.get("versions", {}).get(resolved_version, {})
        license_str = vinfo.get("license") or meta.get("license")
        repo_url = vinfo.get("repository") or meta.get("repository")

        # No repository: create license-only metadata
        if not repo_url:
            logger.warning(
                "Package %s@%s has no repository, creating license-only node",
                dep_spec.name,
                resolved_version,
            )
            cache_dir.mkdir(parents=True, exist_ok=True)
            meta_out = {
                "name": dep_spec.name,
                "resolved_version": resolved_version,
                "license": license_str,
                "repository": None,
                "license_only": True,
            }
            self._write_metadata(cache_dir, meta_out)
            return cache_dir

        # Repository available: clone and checkout nearest to release time
        release_time_iso = meta.get("time", {}).get(resolved_version)

        try:
            self._clone_and_checkout_by_time(repo_url, cache_dir, release_time_iso)
        except Exception as e:
            logger.error("Failed to clone repository for %s: %s", dep_spec.name, e)
            return None

        # Write metadata
        meta_out = {
            "name": dep_spec.name,
            "resolved_version": resolved_version,
            "license": license_str,
            "repository": repo_url,
            "release_time": release_time_iso,
            "license_only": False,
        }
        self._write_metadata(cache_dir, meta_out)

        logger.info("Successfully fetched %s@%s to %s", dep_spec.name, resolved_version, cache_dir)
        return cache_dir

    def _fetch_ohpm_metadata(self, pkg_name: str) -> Dict[str, Any]:
        """Fetch package metadata from OHPM registry.

        Args:
            pkg_name: Package name (may include scope like @scope/package).

        Returns:
            Dict[str, Any]: Package metadata from registry.

        Raises:
            requests.RequestException: If HTTP request fails.
            ValueError: If response is not valid JSON.
        """
        import requests

        # Scope names need URL encoding similar to npm registry
        encoded = quote(pkg_name, safe="@/")
        url = f"{self.REGISTRY_BASE}/{encoded}"

        logger.debug("Fetching OHPM metadata from %s", url)

        response = requests.get(url, timeout=20)
        response.raise_for_status()

        return response.json()

    def _resolve_version(self, meta: Dict[str, Any], requested: Optional[str]) -> str:
        """Resolve version string to exact version.

        Resolution priority:
        1. Exact match
        2. dist-tags.latest
        3. Semantic version sort (if packaging available)
        4. Lexical sort

        Args:
            meta: Package metadata from registry.
            requested: Requested version (may be range or tag).

        Returns:
            str: Resolved exact version.

        Raises:
            ValueError: If no versions available.
        """
        versions = list(meta.get("versions", {}).keys())
        if not versions:
            raise ValueError("No versions available in metadata")

        # Exact match
        if requested and requested in meta.get("versions", {}):
            logger.debug("Exact version match: %s", requested)
            return requested

        # dist-tags.latest
        dist_tags = meta.get("dist-tags", {}) or {}
        latest = dist_tags.get("latest")
        if latest:
            logger.debug("Using dist-tags.latest: %s", latest)
            return latest

        # Semantic version sort (if packaging available)
        if HAS_PACKAGING and PKGVERSION is not None:
            try:
                versions.sort(key=PKGVERSION)
                logger.debug("Using semantic version sort, latest: %s", versions[-1])
                return versions[-1]
            except Exception:
                logger.debug("Semantic version sort failed, falling back to lexical")

        # Lexical sort fallback
        versions.sort()
        logger.debug("Using lexical sort, latest: %s", versions[-1])
        return versions[-1]

    def _clone_and_checkout_by_time(
        self, repo_url: str, target_dir: Path, release_time_iso: Optional[str]
    ) -> None:
        """Clone repository and checkout commit closest to release time.

        This is critical for accurate version matching:
        1. Clone full repository (not shallow) for accurate commit history
        2. Find commit closest to release time using git rev-list
        3. Prefer commit before release time, fallback to after

        Args:
            repo_url: Git repository URL.
            target_dir: Target directory for clone.
            release_time_iso: ISO timestamp of package release.

        Raises:
            RuntimeError: If git clone fails.
        """
        if target_dir.exists():
            logger.debug("Target directory exists, skipping clone: %s", target_dir)
            return

        target_dir.parent.mkdir(parents=True, exist_ok=True)

        # Clone full repository (not shallow) for accurate time-based checkout
        cmd = ["git", "clone", repo_url, str(target_dir)]
        logger.info("Cloning repository (full clone for time-based checkout): %s", repo_url)
        logger.debug("Command: %s", " ".join(cmd))

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout for large repos
            check=False,
        )

        if result.returncode != 0:
            stderr = result.stderr or ""
            # Handle concurrent clone races gracefully. When another process
            # has already cloned into target_dir, git reports that the
            # destination path exists and is not empty. In this case we first
            # validate the directory as a Hvigor cache (using .git and/or
            # .hvigor_meta.json) before deciding whether it is safe to reuse.
            if "already exists and is not an empty directory" in stderr:
                if self._is_valid_cache(target_dir):
                    logger.warning(
                        "git clone for %s reported existing non-empty target %s; "
                        "reusing existing cache populated by another process",
                        repo_url,
                        target_dir,
                    )
                    return

                logger.warning(
                    "git clone for %s hit existing non-empty target %s but cache "
                    "did not pass validation; treating as failure",
                    repo_url,
                    target_dir,
                )

            raise RuntimeError(f"git clone failed: {stderr}")

        if not release_time_iso:
            logger.debug("No release time provided, keeping HEAD")
            return

        # Convert time to strict ISO format for git
        try:
            dt = datetime.fromisoformat(release_time_iso.replace("Z", "+00:00"))
            iso = dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            iso = release_time_iso
            logger.debug("Using release time as-is: %s", iso)

        # Find commit immediately before release time
        cmd_before = [
            "git",
            "-C",
            str(target_dir),
            "rev-list",
            "-n",
            "1",
            f"--before={iso}",
            "HEAD",
        ]
        logger.debug("Finding commit before release: %s", " ".join(cmd_before))

        result = subprocess.run(cmd_before, capture_output=True, text=True, check=False)
        commit = result.stdout.strip()

        if not commit:
            # Fallback: first commit after release time
            logger.debug("No commit before release, trying after")
            cmd_after = [
                "git",
                "-C",
                str(target_dir),
                "rev-list",
                "-n",
                "1",
                f"--after={iso}",
                "HEAD",
            ]
            result = subprocess.run(cmd_after, capture_output=True, text=True, check=False)
            commit = result.stdout.strip()

        if commit:
            logger.info("Checking out commit closest to release time: %s", commit)
            cmd_checkout = ["git", "-C", str(target_dir), "checkout", "-q", commit]
            result = subprocess.run(cmd_checkout, capture_output=True, text=True, check=False)

            if result.returncode != 0:
                logger.warning("git checkout %s failed: %s", commit, result.stderr)
        else:
            logger.warning("Could not find suitable commit for release time %s", iso)

    def _write_metadata(self, cache_dir: Path, metadata: Dict[str, Any]) -> None:
        """Write metadata file to cache directory.

        Args:
            cache_dir: Cache directory.
            metadata: Metadata to write.
        """
        meta_file = cache_dir / ".hvigor_meta.json"
        try:
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
            logger.debug("Wrote metadata to %s", meta_file)
        except Exception as e:
            logger.warning("Failed to write metadata: %s", e)

    def _handle_local_dependency(self, dep_spec: DependencySpec) -> Optional[Path]:
        """Handle local dependency reference.

        Args:
            dep_spec: Dependency specification.

        Returns:
            Optional[Path]: Local path if valid, None otherwise.
        """
        local_path = dep_spec.metadata.get("local_path")
        if not local_path:
            logger.error(
                "No local_path provided for local dependency: %s", dep_spec.name
            )
            return None

        path = Path(local_path)
        if not path.exists():
            logger.error("Local dependency path does not exist: %s", path)
            return None

        logger.info("Using local dependency: %s at %s", dep_spec.name, path)
        return path

    def get_cache_dir(self, dep_spec: DependencySpec) -> Path:
        """Get cache directory for Hvigor dependency.

        Uses format: hvigor/<package_name>/<version>

        Args:
            dep_spec: Dependency specification.

        Returns:
            Path: Cache directory path.
        """
        # Sanitize package name (replace @ and / with _)
        safe_name = dep_spec.name.replace("@", "").replace("/", "_")
        version = dep_spec.version or "latest"

        return self.cache_root / self.ECOSYSTEM / safe_name / version

    def _get_ohpm_cache_dir(self, pkg_name: str, resolved_version: str) -> Path:
        """Get cache directory for an OHPM dependency using resolved version.

        This helper mirrors get_cache_dir but always keys the cache by the
        concrete resolved version instead of the requested range. This avoids
        duplicated checkouts when multiple semver ranges map to the same
        release.

        Args:
            pkg_name: Package name as used in the OHPM registry.
            resolved_version: Concrete version string resolved from metadata.

        Returns:
            Path: Cache directory path for the resolved version.
        """
        safe_name = pkg_name.replace("@", "").replace("/", "_")
        return self.cache_root / self.ECOSYSTEM / safe_name / resolved_version
