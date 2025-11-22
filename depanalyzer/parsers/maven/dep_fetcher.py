"""Maven dependency fetcher."""

from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path
from typing import Optional

from depanalyzer.parsers.base import BaseDepFetcher, DependencySpec

logger = logging.getLogger("depanalyzer.parsers.maven.dep_fetcher")


class MavenDepFetcher(BaseDepFetcher):
    """Fetch Maven artifacts (preferring sources) into cache."""

    NAME = "maven_dep_fetcher"
    ECOSYSTEM = "maven"
    DEFAULT_REPO = "https://repo1.maven.org/maven2"

    def can_handle(self, dep_spec: DependencySpec) -> bool:
        return (dep_spec.ecosystem or "").lower() == "maven"

    def fetch(self, dep_spec: DependencySpec) -> Optional[Path]:
        logger.info("Fetching Maven dependency: %s", dep_spec.name)
        group_id, artifact_id = _split_name(dep_spec.name, dep_spec.metadata)
        version = dep_spec.version or dep_spec.metadata.get("version") or ""
        if not group_id or not artifact_id or not version:
            logger.error("Missing groupId/artifactId/version for Maven fetch: %s", dep_spec)
            return None

        packaging = dep_spec.metadata.get("packaging") or "jar"
        repo = dep_spec.metadata.get("repository") or self.DEFAULT_REPO
        cache_dir = self.get_cache_dir(dep_spec, group_id=group_id, version=version)

        if cache_dir.exists() and any(cache_dir.iterdir()):
            logger.info("Using cached Maven dependency at %s", cache_dir)
            return cache_dir

        cache_dir.mkdir(parents=True, exist_ok=True)

        base_path = "/".join(
            [
                repo.rstrip("/"),
                group_id.replace(".", "/"),
                artifact_id,
                version,
            ]
        )

        sources_url = f"{base_path}/{artifact_id}-{version}-sources.{packaging}"
        binary_url = f"{base_path}/{artifact_id}-{version}.{packaging}"
        pom_url = f"{base_path}/{artifact_id}-{version}.pom"

        sources_path = cache_dir / f"{artifact_id}-{version}-sources.{packaging}"
        binary_path = cache_dir / f"{artifact_id}-{version}.{packaging}"
        pom_path = cache_dir / "pom.xml"

        fetched = False
        if self.download_file(sources_url, sources_path):
            _safe_extract(sources_path, cache_dir)
            fetched = True
        elif self.download_file(binary_url, binary_path):
            _safe_extract(binary_path, cache_dir)
            fetched = True

        if self.download_file(pom_url, pom_path):
            fetched = True

        if not fetched:
            logger.error("Failed to fetch Maven dependency %s from %s", dep_spec.name, repo)
            shutil.rmtree(cache_dir, ignore_errors=True)
            return None

        return cache_dir

    def get_cache_dir(
        self, dep_spec: DependencySpec, group_id: str | None = None, version: str | None = None
    ) -> Path:
        group = group_id or dep_spec.metadata.get("group_id") or "unknown_group"
        version_value = version or dep_spec.version or dep_spec.metadata.get("version") or "unspecified"
        name_part = dep_spec.metadata.get("artifact_id") or dep_spec.name
        safe_name = name_part.replace("/", "_").replace(":", "_")
        return self.cache_root / self.ECOSYSTEM / group.replace(".", "/") / safe_name / version_value


def _split_name(name: str, metadata: dict) -> tuple[str, str]:
    if ":" in name:
        group_id, artifact_id = name.split(":", 1)
    else:
        group_id = metadata.get("group_id") or ""
        artifact_id = metadata.get("artifact_id") or name
    return group_id, artifact_id


def _safe_extract(archive_path: Path, target_dir: Path) -> None:
    if not archive_path.exists():
        return
    try:
        with zipfile.ZipFile(archive_path, "r") as zip_ref:
            zip_ref.extractall(target_dir)
    except (zipfile.BadZipFile, OSError) as exc:
        logger.warning("Failed to extract %s: %s", archive_path, exc)


__all__ = ["MavenDepFetcher"]
