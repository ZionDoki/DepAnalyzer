"""Hvigor/OHPM dependency fetcher.

Fetches Hvigor/ohpm packages from the OpenHarmony package registry.
Includes special logic for time-based version resolution and license-only mode.
"""

import json
import logging
import subprocess
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests

from core.dependency import DependencyResult, DependencySpec, DependencyType
from parsers.deps.fetchers.base import BaseFetcher

logger = logging.getLogger("depanalyzer.fetchers.hvigor")

# Optional semantic version sorter from packaging; fall back gracefully
try:
    PKGVERSION = getattr(import_module("packaging.version"), "Version")
except (ImportError, AttributeError):  # pragma: no cover
    PKGVERSION = None  # type: ignore[assignment]


class HvigorFetcher(BaseFetcher):
    """Fetcher for the Hvigor/OHPM ecosystem.

    Fetches packages from https://ohpm.openharmony.cn/ohpm registry.

    Special features:
    - Time-based version resolution: finds git commit closest to release time
    - License-only mode: handles packages without source repositories
    - Full clone for accurate version matching
    """

    supports_types = (DependencyType.HVIGOR,)
    REGISTRY_BASE = "https://ohpm.openharmony.cn/ohpm"

    def fetch(self, spec: DependencySpec, cache_root: Path) -> DependencyResult:
        """Fetch a Hvigor/ohpm package.

        Fetches package metadata from registry and clones repository if available.
        For packages without repositories, returns license-only result.

        Args:
            spec: Dependency specification.
            cache_root: Root directory for caching dependencies.

        Returns:
            DependencyResult: Fetch result with local_path or license info.
        """
        cache_dir = self.get_cache_dir(spec, cache_root)
        self.ensure_dir(cache_dir.parent)

        # Fetch metadata from registry
        try:
            meta = self._fetch_meta(spec.name)
        except (requests.RequestException, ValueError) as err:
            logger.error("Failed to fetch metadata for %s: %s", spec.name, err)
            return DependencyResult(spec=spec, success=False, error=err)

        # Resolve version
        try:
            resolved_version = self._resolve_version(meta, spec.version)
        except ValueError as err:
            logger.error("Failed to resolve version for %s: %s", spec.name, err)
            return DependencyResult(spec=spec, success=False, error=err)

        vinfo = (meta.get("versions", {}) or {}).get(resolved_version, {})
        license_str = vinfo.get("license") or meta.get("license")
        repo_url: Optional[str] = vinfo.get("repository") or meta.get("repository")

        # No repository: produce a license-only node
        if not repo_url:
            logger.info(
                "Package %s has no repository, creating license-only node", spec.name
            )
            meta_out = {
                "name": spec.name,
                "resolved_version": resolved_version,
                "license": license_str,
                "repository": None,
            }
            return DependencyResult(
                spec=spec,
                success=True,
                local_path=None,
                discovered_deps=[],
                metadata=meta_out,
                license_only=True,
            )

        # Repository available: clone and checkout nearest to release time
        release_time_iso = (meta.get("time", {}) or {}).get(resolved_version)
        try:
            self._clone_and_checkout(repo_url, cache_dir, release_time_iso)
        except (RuntimeError, OSError) as err:
            logger.error("Failed to clone repository for %s: %s", spec.name, err)
            return DependencyResult(spec=spec, success=False, error=err)

        # Write metadata file
        meta_out = {
            "name": spec.name,
            "resolved_version": resolved_version,
            "license": license_str,
            "repository": repo_url,
            "release_time": release_time_iso,
        }
        try:
            with open(cache_dir / ".hvigor_meta.json", "w", encoding="utf-8") as f:
                json.dump(meta_out, f, ensure_ascii=False, indent=2)
        except (OSError, TypeError) as e:
            logger.warning("Failed to write metadata for %s: %s", spec.name, e)

        logger.info("Successfully fetched %s@%s to %s", spec.name, resolved_version, cache_dir)
        return DependencyResult(
            spec=spec,
            success=True,
            local_path=cache_dir,
            discovered_deps=[],
            metadata=meta_out,
            license_only=False,
        )

    def _fetch_meta(self, pkg_name: str) -> Dict[str, Any]:
        """Fetch package metadata from OHPM registry.

        Args:
            pkg_name: Package name (may include scope like @scope/package).

        Returns:
            Dict[str, Any]: Package metadata from registry.

        Raises:
            requests.RequestException: If HTTP request fails.
            ValueError: If response is not valid JSON.
        """
        # Scope names need URL encoding similar to npm registry
        encoded = quote(pkg_name, safe="@/")
        url = f"{self.REGISTRY_BASE}/{encoded}"
        logger.debug("Fetching Hvigor meta from %s", url)
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        return r.json()

    def _resolve_version(self, meta: Dict[str, Any], req: str) -> str:
        """Resolve version string to exact version.

        Resolution priority:
        1. Exact match
        2. dist-tags.latest
        3. Semantic version sort (if packaging available)
        4. Lexical sort

        Args:
            meta: Package metadata from registry.
            req: Requested version (may be range or tag).

        Returns:
            str: Resolved exact version.

        Raises:
            ValueError: If no versions available.
        """
        versions = list((meta.get("versions", {}) or {}).keys())
        if not versions:
            raise ValueError("No versions in metadata")

        # Exact match wins
        if req and req in meta.get("versions", {}):
            return req

        # dist-tags.latest
        dist_tags = meta.get("dist-tags", {}) or {}
        latest = dist_tags.get("latest")
        if latest:
            return latest

        # Fallback: sort by semantic version using packaging if available
        if PKGVERSION is not None:
            try:
                versions.sort(key=PKGVERSION)
                return versions[-1]
            except Exception:
                pass  # Fall through to lexical sort

        # Final fallback: lexical sort
        versions.sort()
        return versions[-1]

    def _clone_and_checkout(
        self, repo_url: str, target_dir: Path, release_time_iso: Optional[str]
    ) -> None:
        """Clone repository and checkout commit closest to release time.

        This is the critical logic for accurate version matching:
        1. Clone full repository (not shallow)
        2. Find commit closest to release time using git rev-list
        3. Prefer commit before release time, fallback to after

        Args:
            repo_url: Git repository URL.
            target_dir: Target directory for clone.
            release_time_iso: ISO timestamp of package release.

        Raises:
            RuntimeError: If git clone fails.
        """
        target_dir_str = str(target_dir)

        # Clone (full clone for accurate commit selection)
        cmd = ["git", "clone", repo_url, target_dir_str]
        logger.debug("Cloning repository: %s", " ".join(cmd))
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            raise RuntimeError(f"git clone failed: {res.stderr}")

        if not release_time_iso:
            logger.debug("No release time provided, keeping HEAD")
            return  # No release time; keep default HEAD

        # Convert time to strict ISO format for git
        try:
            # For inputs like 2025-03-04T16:22:30.911Z
            dt = datetime.fromisoformat(release_time_iso.replace("Z", "+00:00"))
            iso = dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            iso = release_time_iso

        # Prefer the commit immediately before the release time
        cmd = [
            "git",
            "-C",
            target_dir_str,
            "rev-list",
            "-n",
            "1",
            f"--before={iso}",
            "HEAD",
        ]
        logger.debug("Finding commit before release: %s", " ".join(cmd))
        rev = subprocess.run(cmd, capture_output=True, text=True, check=False)
        commit = (rev.stdout or "").strip()

        if not commit:
            # Fallback to first commit after the release time
            logger.debug("No commit before release, trying after")
            cmd2 = [
                "git",
                "-C",
                target_dir_str,
                "rev-list",
                "-n",
                "1",
                f"--after={iso}",
                "HEAD",
            ]
            rev2 = subprocess.run(cmd2, capture_output=True, text=True, check=False)
            commit = (rev2.stdout or "").strip()

        if commit:
            logger.debug("Checking out commit: %s", commit)
            co = subprocess.run(
                ["git", "-C", target_dir_str, "checkout", "-q", commit],
                capture_output=True,
                text=True,
                check=False,
            )
            if co.returncode != 0:
                logger.warning("git checkout %s failed: %s", commit, co.stderr)
        else:
            logger.warning("Could not find suitable commit for release time %s", iso)
