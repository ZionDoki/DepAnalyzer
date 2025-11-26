"""Hvigor ecosystem dependency fetcher.

Handles fetching of HarmonyOS dependencies from:
- OHPM (OpenHarmony Package Manager) registry
- Git repositories (for open-source HAR/HSP libraries)

Special features:
- Time-based version resolution: finds git commit closest to release time
- License-only mode: handles packages without source repositories
- Full clone for accurate version matching
"""

# Dependency fetching wraps external network/process calls defensively.


import json
import logging
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

logger = logging.getLogger("depanalyzer.parsers.hvigor.dep_fetcher")

# Optional requests library
try:
    import requests

    HAS_REQUESTS = True
except ImportError:
    requests = None
    HAS_REQUESTS = False
    logger.warning(
        "requests library not available, OHPM registry fetching will be limited"
    )

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
    # Negative cache to avoid repeated registry/clone attempts for failures
    _failure_cache: Dict[Tuple[str, str, str], str] = {}

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

        metadata = dep_spec.metadata or {}
        registry_type = self._get_registry_type(dep_spec)
        path_hint = self._get_path_hint(dep_spec, metadata)
        workspace_root = self._get_workspace_root(metadata)
        fail_key = (dep_spec.name, dep_spec.version or "", registry_type)

        if fail_key in self._failure_cache:
            logger.warning(
                "Skipping %s@%s (%s) due to previous failure: %s",
                dep_spec.name,
                dep_spec.version or "latest",
                registry_type,
                self._failure_cache[fail_key],
            )
            return None

        # Local/self dependencies should never trigger network fetches
        local_path = self._resolve_local_dependency(
            dep_spec, workspace_root=workspace_root, path_hint=path_hint
        )
        if local_path is not None:
            return local_path

        # Git-based dependencies use the generic cache-dir layout which
        # includes the requested version string. This is safe because Git
        # repositories already encode their own versioning scheme (tags/refs).
        dep_type = metadata.get("type", registry_type)
        if dep_type == "git" or registry_type == "git" or (
            dep_spec.source_url and ".git" in dep_spec.source_url
        ):
            cache_dir = self.get_cache_dir(dep_spec)

            if cache_dir.exists() and self._is_valid_cache(cache_dir):
                logger.info("Using cached Git dependency: %s", cache_dir)
                return cache_dir

            try:
                return self._fetch_from_git(
                    dep_spec, cache_dir, sparse_paths=[path_hint] if path_hint else None
                )
            except Exception as exc:  # noqa: BLE001
                self._failure_cache[fail_key] = str(exc)
                logger.error("Failed to fetch Git dependency %s: %s", dep_spec.name, exc)
                return None

        # OHPM registry dependencies are resolved to an exact version first
        # and then cached under hvigor/<safe_name>/<resolved_version> so that
        # different semver ranges which resolve to the same concrete version
        # reuse the same on-disk checkout.
        try:
            return self._fetch_from_ohpm(dep_spec, path_hint=path_hint)
        except Exception as exc:  # noqa: BLE001
            self._failure_cache[fail_key] = str(exc)
            logger.error("Failed to fetch Hvigor dependency %s: %s", dep_spec.name, exc)
            return None

    def _is_valid_cache(self, cache_dir: Path) -> bool:
        """Check if cached directory is valid.

        Args:
            cache_dir: Cache directory to check.

        Returns:
            bool: True if cache is valid.
        """
        # Check for metadata file or git directory
        return (cache_dir / ".hvigor_meta.json").exists() or (
            cache_dir / ".git"
        ).exists()

    def _get_registry_type(self, dep_spec: DependencySpec) -> str:
        """Return registry type hint from metadata."""
        metadata = dep_spec.metadata or {}
        return (
            metadata.get("registry_type")
            or metadata.get("type")
            or dep_spec.ecosystem
            or "ohpm"
        )

    def _get_path_hint(self, dep_spec: DependencySpec, metadata: Dict[str, Any]) -> Optional[str]:
        """Return best-effort path hint for mono-repo sparse checkout."""
        hint = metadata.get("path_hint")
        if hint:
            return hint
        name = dep_spec.name or ""
        return name.split("/")[-1] if "/" in name else name

    def _get_workspace_root(self, metadata: Dict[str, Any]) -> Optional[Path]:
        """Extract workspace root from dependency metadata."""
        root = metadata.get("workspace_root")
        if not root:
            return None
        try:
            return Path(root).resolve()
        except OSError:
            return None

    def _resolve_local_dependency(
        self,
        dep_spec: DependencySpec,
        workspace_root: Optional[Path],
        path_hint: Optional[str],
    ) -> Optional[Path]:
        """Resolve local/self dependency without network access."""
        metadata = dep_spec.metadata or {}
        registry_type = self._get_registry_type(dep_spec)
        version = dep_spec.version or ""

        is_local = (
            registry_type == "local"
            or metadata.get("type") == "local"
            or version.startswith("file:")
        )
        if not is_local:
            return None

        candidates: List[str] = []
        if metadata.get("local_path"):
            candidates.append(str(metadata["local_path"]))
        if version.startswith("file:"):
            candidates.append(version.replace("file:", "", 1))
        if path_hint:
            candidates.append(path_hint)

        for cand in candidates:
            if not cand:
                continue
            path = Path(cand)
            if not path.is_absolute() and workspace_root:
                path = workspace_root / cand
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved.exists():
                logger.info(
                    "Using local Hvigor dependency %s at %s", dep_spec.name, resolved
                )
                return resolved

        logger.warning(
            "Local dependency %s declared but no valid path found (candidates=%s)",
            dep_spec.name,
            candidates,
        )
        return None

    def _fetch_from_git(
        self, dep_spec: DependencySpec, cache_dir: Path, sparse_paths: Optional[List[str]] = None
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
            try:
                self._configure_sparse_checkout(cache_dir, sparse_paths)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Sparse checkout setup failed for %s (continuing): %s",
                    cache_dir,
                    exc,
                )
            logger.info("Successfully fetched %s to %s", dep_spec.name, cache_dir)
            return cache_dir

        logger.error("Failed to fetch %s from %s", dep_spec.name, url)
        return None

    def _fetch_from_ohpm(
        self, dep_spec: DependencySpec, path_hint: Optional[str] = None
    ) -> Optional[Path]:
        """Fetch dependency from OHPM registry.

        Args:
            dep_spec: Dependency specification.
            path_hint: Optional sub-path hint for sparse checkout.

        Returns:
            Optional[Path]: Local path if successful, None otherwise.
        """
        if not HAS_REQUESTS:
            logger.error("requests library required for OHPM fetching")
            return None

        logger.info(
            "Fetching from OHPM registry: %s@%s", dep_spec.name, dep_spec.version
        )

        # Fetch metadata from registry
        meta = self._fetch_ohpm_metadata(dep_spec.name)

        # Resolve version (may be a semver range or tag). The resolved version
        # is used as the canonical cache key so that multiple range expressions
        # resolving to the same concrete version share a single checkout.
        resolved_version = self._resolve_version(meta, dep_spec.version)

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
        vinfo = meta.get("versions", {}).get(resolved_version, {}) or {}
        license_str = vinfo.get("license") or meta.get("license")
        repo_url = vinfo.get("repository") or meta.get("repository")
        dist_info = vinfo.get("dist") or {}
        tarball_url = dist_info.get("tarball")

        # Prefer registry tarball when available to avoid cloning monorepos
        if tarball_url:
            extracted = self._download_and_extract_tarball(tarball_url, cache_dir)
            if extracted:
                meta_out = {
                    "name": dep_spec.name,
                    "resolved_version": resolved_version,
                    "license": license_str,
                    "repository": repo_url,
                    "release_time": meta.get("time", {}).get(resolved_version),
                    "license_only": False,
                    "fetched_via": "tarball",
                    "tarball": tarball_url,
                    "package_root": str(extracted),
                }
                self._write_metadata(cache_dir, meta_out)
                logger.info(
                    "Successfully fetched %s@%s via tarball to %s",
                    dep_spec.name,
                    resolved_version,
                    cache_dir,
                )
                return cache_dir
            logger.debug("Tarball fetch failed for %s, falling back to git", dep_spec.name)

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
                "fetched_via": "metadata_only",
            }
            self._write_metadata(cache_dir, meta_out)
            return cache_dir

        # Repository available: clone and checkout nearest to release time
        release_time_iso = meta.get("time", {}).get(resolved_version)

        self._clone_and_checkout_by_time(
            repo_url, cache_dir, release_time_iso, sparse_paths=[path_hint] if path_hint else None
        )

        # Write metadata
        meta_out = {
            "name": dep_spec.name,
            "resolved_version": resolved_version,
            "license": license_str,
            "repository": repo_url,
            "release_time": release_time_iso,
            "license_only": False,
            "fetched_via": "git",
        }
        if path_hint:
            meta_out["package_root"] = str(cache_dir / path_hint)
        self._write_metadata(cache_dir, meta_out)

        logger.info(
            "Successfully fetched %s@%s to %s",
            dep_spec.name,
            resolved_version,
            cache_dir,
        )
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
        # Scope names need URL encoding similar to npm registry
        encoded = quote(pkg_name, safe="@/")
        url = f"{self.REGISTRY_BASE}/{encoded}"

        logger.debug("Fetching OHPM metadata from %s", url)

        if requests is None:
            raise RuntimeError(
                "requests library is required for OHPM metadata fetching"
            )

        response = requests.get(url, timeout=20)
        response.raise_for_status()

        return response.json()

    def _download_and_extract_tarball(
        self, tarball_url: str, target_dir: Path
    ) -> Optional[Path]:
        """Download and extract package tarball to cache directory."""
        if not HAS_REQUESTS:
            return None

        target_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_tar = Path(tmpdir) / "package.tgz"
            try:
                with requests.get(tarball_url, stream=True, timeout=60) as resp:
                    resp.raise_for_status()
                    with tmp_tar.open("wb") as handle:
                        for chunk in resp.iter_content(chunk_size=8192):
                            if chunk:
                                handle.write(chunk)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Downloading tarball %s failed: %s", tarball_url, exc)
                return None

            try:
                with tarfile.open(tmp_tar, "r:*") as tf:
                    members = tf.getmembers()
                    for member in members:
                        member_path = Path(member.name)
                        if ".." in member_path.parts:
                            logger.warning(
                                "Skipping unsafe tarball member %s from %s",
                                member.name,
                                tarball_url,
                            )
                            return None
                    tf.extractall(target_dir)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Extracting tarball %s failed: %s", tarball_url, exc)
                return None

        # Best-effort guess of package root (first directory, else target_dir)
        try:
            dirs = [p for p in target_dir.iterdir() if p.is_dir()]
            return dirs[0] if dirs else target_dir
        except OSError:
            return target_dir

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
        self,
        repo_url: str,
        target_dir: Path,
        release_time_iso: Optional[str],
        sparse_paths: Optional[List[str]] = None,
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
        cmd = ["git", "clone", "--filter=blob:none", repo_url, str(target_dir)]
        logger.info(
            "Cloning repository (full clone for time-based checkout): %s", repo_url
        )
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

        try:
            self._configure_sparse_checkout(target_dir, sparse_paths)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Sparse checkout setup failed for %s (continuing): %s",
                target_dir,
                exc,
            )

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
            result = subprocess.run(
                cmd_after, capture_output=True, text=True, check=False
            )
            commit = result.stdout.strip()

        if commit:
            logger.info("Checking out commit closest to release time: %s", commit)
            cmd_checkout = ["git", "-C", str(target_dir), "checkout", "-q", commit]
            result = subprocess.run(
                cmd_checkout, capture_output=True, text=True, check=False
            )

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
        metadata = dep_spec.metadata or {}
        workspace_root = self._get_workspace_root(metadata)
        local_path = metadata.get("local_path")
        if not local_path:
            logger.error(
                "No local_path provided for local dependency: %s", dep_spec.name
            )
            return None

        path = Path(local_path)
        if not path.is_absolute() and workspace_root:
            path = workspace_root / local_path
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

    def _configure_sparse_checkout(
        self, repo_dir: Path, sparse_paths: Optional[List[str]]
    ) -> None:
        """Enable sparse checkout for the requested sub-paths."""
        if not sparse_paths:
            return

        try:
            repo_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return

        cmd_init = [
            "git",
            "-C",
            str(repo_dir),
            "sparse-checkout",
            "init",
            "--no-cone",
        ]
        subprocess.run(cmd_init, capture_output=True, text=True, check=False)

        cmd_set = ["git", "-C", str(repo_dir), "sparse-checkout", "set", *sparse_paths]
        subprocess.run(cmd_set, capture_output=True, text=True, check=False)
