"""NPM ecosystem dependency fetcher.

Handles fetching of npm packages from:
- npm registry (registry.npmjs.org)
- Git repositories
- Local file dependencies
"""

import json
import logging
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from depanalyzer.parsers.base import BaseDepFetcher, DependencySpec, FetchError

logger = logging.getLogger("depanalyzer.parsers.npm.dep_fetcher")

# Optional requests library
try:
    import requests

    HAS_REQUESTS = True
except ImportError:
    requests = None
    HAS_REQUESTS = False
    logger.warning(
        "requests library not available, npm registry fetching will be limited"
    )

# Optional semantic version handling
try:
    from packaging.version import Version as PkgVersion
    from packaging.specifiers import SpecifierSet

    HAS_PACKAGING = True
except ImportError:
    PkgVersion = None
    SpecifierSet = None
    HAS_PACKAGING = False
    logger.debug("packaging library not available, will use basic version matching")


class NpmDepFetcher(BaseDepFetcher):
    """Dependency fetcher for npm ecosystem.

    Handles npm packages from the official registry, Git repositories,
    and local file dependencies.
    """

    NAME = "npm_dep_fetcher"
    ECOSYSTEM = "npm"
    REGISTRY_BASE = "https://registry.npmjs.org"

    # Cache for failed fetches to avoid repeated attempts
    _FAILURE_CACHE_MAX_SIZE = 1000
    _failure_cache: Dict[Tuple[str, str], str] = {}

    @classmethod
    def _add_to_failure_cache(cls, key: Tuple[str, str], reason: str) -> None:
        """Add failure record to cache with size limit (FIFO eviction)."""
        if len(cls._failure_cache) >= cls._FAILURE_CACHE_MAX_SIZE:
            try:
                oldest_key = next(iter(cls._failure_cache))
                del cls._failure_cache[oldest_key]
            except (StopIteration, KeyError):
                pass
        cls._failure_cache[key] = reason

    def can_handle(self, dep_spec: DependencySpec) -> bool:
        """Check if this fetcher can handle the dependency.

        Args:
            dep_spec: Dependency specification.

        Returns:
            bool: True if ecosystem is 'npm' or 'nodejs'.
        """
        return dep_spec.ecosystem in (self.ECOSYSTEM, "nodejs", "node")

    def fetch(self, dep_spec: DependencySpec) -> Optional[Path]:
        """Fetch an npm dependency.

        Args:
            dep_spec: Dependency specification.

        Returns:
            Optional[Path]: Local path to fetched dependency, None if failed.
        """
        logger.info("Fetching npm dependency: %s@%s", dep_spec.name, dep_spec.version)

        metadata = dep_spec.metadata or {}
        version = dep_spec.version or "latest"
        fail_key = (dep_spec.name, version)

        # Check failure cache
        if fail_key in self._failure_cache:
            logger.warning(
                "Skipping %s@%s due to previous failure: %s",
                dep_spec.name,
                version,
                self._failure_cache[fail_key],
            )
            return None

        # Handle local file dependencies
        if version.startswith("file:"):
            return self._fetch_local(dep_spec, version)

        # Handle Git dependencies
        if self._is_git_dependency(dep_spec, version):
            return self._fetch_from_git(dep_spec)

        # Handle registry dependencies
        try:
            return self._fetch_from_registry(dep_spec)
        except Exception as exc:
            self._add_to_failure_cache(fail_key, str(exc))
            logger.error("Failed to fetch npm dependency %s: %s", dep_spec.name, exc)
            return None

    def _is_git_dependency(self, dep_spec: DependencySpec, version: str) -> bool:
        """Check if dependency is a Git-based dependency.

        Args:
            dep_spec: Dependency specification.
            version: Version string.

        Returns:
            bool: True if this is a Git dependency.
        """
        if dep_spec.source_url and ".git" in dep_spec.source_url:
            return True
        if version.startswith(("git:", "git+", "github:", "gitlab:", "bitbucket:")):
            return True
        if "github.com" in version or "gitlab.com" in version:
            return True
        return False

    def _fetch_local(self, dep_spec: DependencySpec, version: str) -> Optional[Path]:
        """Fetch a local file dependency.

        Args:
            dep_spec: Dependency specification.
            version: Version string starting with "file:".

        Returns:
            Optional[Path]: Local path if valid, None otherwise.
        """
        local_path = version.replace("file:", "", 1)
        path = Path(local_path)

        # Try to resolve relative to workspace root if provided
        metadata = dep_spec.metadata or {}
        workspace_root = metadata.get("workspace_root")
        if workspace_root and not path.is_absolute():
            path = Path(workspace_root) / local_path

        try:
            resolved = path.resolve()
            # Validate path is within workspace to prevent path traversal attacks
            if workspace_root:
                workspace_resolved = Path(workspace_root).resolve()
                if not resolved.is_relative_to(workspace_resolved):
                    logger.warning(
                        "Path traversal attempt blocked for %s: %s escapes workspace %s",
                        dep_spec.name,
                        local_path,
                        workspace_root,
                    )
                    return None
            if resolved.exists():
                logger.info("Using local npm dependency %s at %s", dep_spec.name, resolved)
                return resolved
        except OSError:
            pass

        logger.warning("Local dependency %s not found at %s", dep_spec.name, local_path)
        return None

    def _fetch_from_git(self, dep_spec: DependencySpec) -> Optional[Path]:
        """Fetch dependency from Git repository.

        Args:
            dep_spec: Dependency specification.

        Returns:
            Optional[Path]: Local path if successful, None otherwise.
        """
        url = dep_spec.source_url
        version = dep_spec.version or ""

        # Parse Git URL from version string if needed
        if not url:
            url = self._parse_git_url(version)

        if not url:
            logger.error("No Git URL for dependency: %s", dep_spec.name)
            return None

        cache_dir = self.get_cache_dir(dep_spec)

        if cache_dir.exists() and self._is_valid_cache(cache_dir):
            logger.info("Using cached Git dependency: %s", cache_dir)
            return cache_dir

        # Determine branch/tag
        metadata = dep_spec.metadata or {}
        branch = metadata.get("branch")
        tag = metadata.get("tag")

        # Try to extract tag from version
        if not tag and "#" in version:
            tag = version.split("#")[-1]

        success = self.git_clone(
            url=url,
            target_dir=cache_dir,
            branch=branch,
            tag=tag,
            depth=1,
            force=False,
        )

        if success:
            self._write_metadata(cache_dir, {
                "name": dep_spec.name,
                "version": dep_spec.version,
                "source": "git",
                "repository": url,
            })
            return cache_dir

        return None

    def _parse_git_url(self, version: str) -> Optional[str]:
        """Parse Git URL from version string.

        Args:
            version: Version string that may contain Git URL.

        Returns:
            Optional[str]: Git URL or None.
        """
        # github:user/repo
        if version.startswith("github:"):
            repo = version.replace("github:", "")
            if "#" in repo:
                repo = repo.split("#")[0]
            return f"https://github.com/{repo}.git"

        # gitlab:user/repo
        if version.startswith("gitlab:"):
            repo = version.replace("gitlab:", "")
            if "#" in repo:
                repo = repo.split("#")[0]
            return f"https://gitlab.com/{repo}.git"

        # git+https://... or git://...
        if version.startswith("git+"):
            return version.replace("git+", "")
        if version.startswith("git:"):
            return version.replace("git:", "https:")

        # Direct GitHub/GitLab URLs
        if "github.com" in version or "gitlab.com" in version:
            url = version
            if "#" in url:
                url = url.split("#")[0]
            if not url.endswith(".git"):
                url += ".git"
            return url

        return None

    def _fetch_from_registry(self, dep_spec: DependencySpec) -> Optional[Path]:
        """Fetch dependency from npm registry.

        Args:
            dep_spec: Dependency specification.

        Returns:
            Optional[Path]: Local path if successful, None otherwise.
        """
        if not HAS_REQUESTS:
            logger.error("requests library required for npm registry fetching")
            return None

        logger.info(
            "Fetching from npm registry: %s@%s", dep_spec.name, dep_spec.version
        )

        # Fetch package metadata
        meta = self._fetch_registry_metadata(dep_spec.name)
        if not meta:
            return None

        # Resolve version
        resolved_version = self._resolve_version(meta, dep_spec.version)
        if not resolved_version:
            logger.error(
                "Could not resolve version %s for %s",
                dep_spec.version,
                dep_spec.name,
            )
            return None

        logger.info("Resolved version: %s -> %s", dep_spec.version, resolved_version)

        # Get cache directory based on resolved version
        cache_dir = self._get_registry_cache_dir(dep_spec.name, resolved_version)

        # Check cache
        if cache_dir.exists() and self._is_valid_cache(cache_dir):
            logger.info("Using cached npm dependency: %s", cache_dir)
            return cache_dir

        # Get version info
        version_info = meta.get("versions", {}).get(resolved_version, {})
        if not version_info:
            logger.error("No version info for %s@%s", dep_spec.name, resolved_version)
            return None

        # Get tarball URL
        dist_info = version_info.get("dist", {})
        tarball_url = dist_info.get("tarball")

        if not tarball_url:
            logger.error("No tarball URL for %s@%s", dep_spec.name, resolved_version)
            return None

        # Download and extract tarball
        extracted = self._download_and_extract_tarball(tarball_url, cache_dir)
        if not extracted:
            return None

        # Write metadata
        license_str = version_info.get("license") or meta.get("license")
        repository = version_info.get("repository") or meta.get("repository")
        if isinstance(repository, dict):
            repository = repository.get("url", "")

        self._write_metadata(cache_dir, {
            "name": dep_spec.name,
            "resolved_version": resolved_version,
            "license": license_str,
            "repository": repository,
            "source": "registry",
            "tarball": tarball_url,
            "license_only": False,
        })

        logger.info(
            "Successfully fetched %s@%s to %s",
            dep_spec.name,
            resolved_version,
            cache_dir,
        )
        return cache_dir

    def _fetch_registry_metadata(
        self, pkg_name: str, max_retries: int = 2
    ) -> Optional[Dict[str, Any]]:
        """Fetch package metadata from npm registry with retry support.

        Args:
            pkg_name: Package name (may include scope like @scope/package).
            max_retries: Maximum number of retry attempts for transient errors.

        Returns:
            Optional[Dict]: Package metadata or None if failed.
        """
        import time as _time

        # Encode scoped package names
        encoded = quote(pkg_name, safe="@")
        url = f"{self.REGISTRY_BASE}/{encoded}"

        logger.debug("Fetching npm metadata from %s", url)

        last_error = None
        for attempt in range(max_retries):
            try:
                response = requests.get(url, timeout=(5, 15))
                response.raise_for_status()
                return response.json()
            except requests.RequestException as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2  # Exponential backoff: 2, 4 seconds
                    logger.warning(
                        "Retry %d/%d for %s after error: %s (waiting %ds)",
                        attempt + 1,
                        max_retries,
                        pkg_name,
                        e,
                        wait_time,
                    )
                    _time.sleep(wait_time)
                    continue
            except json.JSONDecodeError as e:
                logger.error("Invalid JSON response for %s: %s", pkg_name, e)
                return None

        logger.error("Failed to fetch metadata for %s: %s", pkg_name, last_error)
        return None

    def _resolve_version(
        self,
        meta: Dict[str, Any],
        requested: Optional[str],
    ) -> Optional[str]:
        """Resolve version string to exact version.

        Args:
            meta: Package metadata from registry.
            requested: Requested version (may be range or tag).

        Returns:
            Optional[str]: Resolved exact version or None.
        """
        versions = list(meta.get("versions", {}).keys())
        if not versions:
            return None

        # Handle None or empty
        if not requested or requested == "latest":
            dist_tags = meta.get("dist-tags", {})
            return dist_tags.get("latest") or (versions[-1] if versions else None)

        # Exact match
        if requested in versions:
            return requested

        # Check dist-tags
        dist_tags = meta.get("dist-tags", {})
        if requested in dist_tags:
            return dist_tags[requested]

        # Try semver matching
        if HAS_PACKAGING:
            resolved = self._resolve_semver(versions, requested)
            if resolved:
                return resolved

        # Handle prerelease tags like "1.4.5-lts.1" - try base version
        # This handles OpenHarmony-specific version formats
        if requested and "-" in requested:
            # Extract base version (e.g., "1.4.5" from "1.4.5-lts.1")
            base_requested = requested.split("-")[0]
            if base_requested in versions:
                logger.info(
                    "Falling back to base version %s for requested %s",
                    base_requested,
                    requested,
                )
                return base_requested
            # Also try semver matching with base version
            if HAS_PACKAGING:
                resolved = self._resolve_semver(versions, base_requested)
                if resolved:
                    logger.info(
                        "Resolved base version %s -> %s for requested %s",
                        base_requested,
                        resolved,
                        requested,
                    )
                    return resolved

        # Fallback: return latest
        return versions[-1] if versions else None

    def _resolve_semver(
        self,
        versions: List[str],
        requested: str,
    ) -> Optional[str]:
        """Resolve semver range to specific version.

        Args:
            versions: Available versions.
            requested: Requested version range.

        Returns:
            Optional[str]: Best matching version or None.
        """
        try:
            # Convert npm semver to Python specifier
            specifier = self._npm_to_python_specifier(requested)
            if not specifier:
                return None

            spec_set = SpecifierSet(specifier)

            # Find matching versions
            matching = []
            for v in versions:
                try:
                    if PkgVersion(v) in spec_set:
                        matching.append(v)
                except Exception:
                    continue

            if matching:
                # Sort and return highest
                matching.sort(key=lambda x: PkgVersion(x))
                return matching[-1]

        except Exception as e:
            logger.debug("Semver resolution failed for %s: %s", requested, e)

        return None

    def _normalize_version_for_python(self, version: str) -> str:
        """Normalize npm version string for Python packaging library.

        Removes or converts npm-specific prerelease tags that Python's
        packaging library doesn't understand.

        Args:
            version: npm version string.

        Returns:
            str: Normalized version string.
        """
        # Remove npm-specific prerelease tags like -lts.1, -beta.1, etc.
        # Python packaging uses different prerelease format
        if "-" in version:
            base = version.split("-")[0]
            return base
        return version

    def _npm_to_python_specifier(self, npm_spec: str) -> Optional[str]:
        """Convert npm version specifier to Python specifier.

        Args:
            npm_spec: npm version specifier.

        Returns:
            Optional[str]: Python specifier or None.
        """
        spec = npm_spec.strip()

        # Handle compound ranges like ">= 2.1.2 < 3.0.0" or ">= 1.4.0 < 2"
        # Split by space and process each part
        if " " in spec:
            parts = spec.split()
            python_parts = []
            i = 0
            while i < len(parts):
                part = parts[i]
                # Handle operators that might be separate from version
                if part in (">=", ">", "<=", "<", "="):
                    if i + 1 < len(parts):
                        version = parts[i + 1]
                        if part == "=":
                            python_parts.append(f"=={version}")
                        else:
                            python_parts.append(f"{part}{version}")
                        i += 2
                        continue
                # Handle combined like ">=2.1.2"
                elif part.startswith((">=", ">", "<=", "<")):
                    python_parts.append(part)
                i += 1
            if python_parts:
                return ",".join(python_parts)

        # Handle caret (^) - compatible with version
        if spec.startswith("^"):
            version = spec[1:]
            # Strip any prerelease tags for range calculation
            base_version = self._normalize_version_for_python(version)
            parts = base_version.split(".")
            if len(parts) >= 1:
                try:
                    major = int(parts[0])
                    if major == 0:
                        # ^0.x.y means >=0.x.y <0.(x+1).0
                        if len(parts) >= 2:
                            minor = int(parts[1])
                            return f">={base_version},<0.{minor+1}.0"
                    else:
                        # ^x.y.z means >=x.y.z <(x+1).0.0
                        return f">={base_version},<{major+1}.0.0"
                except ValueError:
                    pass

        # Handle tilde (~) - approximately equivalent
        if spec.startswith("~"):
            version = spec[1:]
            base_version = self._normalize_version_for_python(version)
            parts = base_version.split(".")
            if len(parts) >= 2:
                try:
                    major, minor = parts[0], int(parts[1])
                    return f">={base_version},<{major}.{minor+1}.0"
                except ValueError:
                    pass

        # Handle ranges
        if spec.startswith(">="):
            return spec
        if spec.startswith(">"):
            return spec
        if spec.startswith("<="):
            return spec
        if spec.startswith("<"):
            return spec
        if spec.startswith("="):
            return f"=={spec[1:]}"

        # Handle x-ranges (1.x, 1.2.x, 2.8.x)
        if "x" in spec.lower() or "*" in spec:
            parts = spec.replace("*", "x").lower().split(".")
            if parts[0] == "x":
                return ">=0.0.0"
            try:
                major = int(parts[0])
                if len(parts) >= 2 and parts[1] == "x":
                    return f">={major}.0.0,<{major+1}.0.0"
                if len(parts) >= 3 and parts[2] == "x":
                    minor = int(parts[1])
                    return f">={major}.{minor}.0,<{major}.{minor+1}.0"
            except ValueError:
                pass

        # Exact version
        return f"=={spec}"

    def _download_and_extract_tarball(
        self,
        tarball_url: str,
        target_dir: Path,
    ) -> Optional[Path]:
        """Download and extract npm package tarball.

        Args:
            tarball_url: URL to the tarball.
            target_dir: Target directory for extraction.

        Returns:
            Optional[Path]: Path to extracted package or None.
        """
        target_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_tar = Path(tmpdir) / "package.tgz"

            try:
                # Download tarball
                with requests.get(tarball_url, stream=True, timeout=(5, 30)) as resp:
                    resp.raise_for_status()
                    with tmp_tar.open("wb") as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
            except Exception as e:
                logger.error("Failed to download tarball %s: %s", tarball_url, e)
                return None

            try:
                # Extract tarball using safe extraction to prevent path traversal
                from depanalyzer.utils.archive_utils import (
                    ArchiveSecurityError,
                    safe_extract_tar,
                )

                safe_extract_tar(tmp_tar, target_dir)
            except ArchiveSecurityError as e:
                logger.warning("Unsafe tarball detected: %s", e)
                return None
            except Exception as e:
                logger.error("Failed to extract tarball: %s", e)
                return None

        # npm tarballs typically extract to a "package" subdirectory
        package_dir = target_dir / "package"
        if package_dir.is_dir():
            return package_dir

        return target_dir

    def _is_valid_cache(self, cache_dir: Path) -> bool:
        """Check if cached directory is valid.

        Args:
            cache_dir: Cache directory to check.

        Returns:
            bool: True if cache is valid.
        """
        # Check for metadata file or package.json
        return (
            (cache_dir / ".npm_meta.json").exists()
            or (cache_dir / "package.json").exists()
            or (cache_dir / "package" / "package.json").exists()
            or (cache_dir / ".git").exists()
        )

    def _write_metadata(self, cache_dir: Path, metadata: Dict[str, Any]) -> None:
        """Write metadata file to cache directory.

        Args:
            cache_dir: Cache directory.
            metadata: Metadata to write.
        """
        meta_file = cache_dir / ".npm_meta.json"
        try:
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
            logger.debug("Wrote metadata to %s", meta_file)
        except Exception as e:
            logger.warning("Failed to write metadata: %s", e)

    def get_cache_dir(self, dep_spec: DependencySpec) -> Path:
        """Get cache directory for npm dependency.

        Args:
            dep_spec: Dependency specification.

        Returns:
            Path: Cache directory path.
        """
        # Sanitize package name (handle scoped packages)
        safe_name = dep_spec.name.replace("@", "").replace("/", "_")
        version = dep_spec.version or "latest"

        return self.cache_root / self.ECOSYSTEM / safe_name / version

    def _get_registry_cache_dir(self, pkg_name: str, resolved_version: str) -> Path:
        """Get cache directory using resolved version.

        Args:
            pkg_name: Package name.
            resolved_version: Resolved version string.

        Returns:
            Path: Cache directory path.
        """
        safe_name = pkg_name.replace("@", "").replace("/", "_")
        return self.cache_root / self.ECOSYSTEM / safe_name / resolved_version
