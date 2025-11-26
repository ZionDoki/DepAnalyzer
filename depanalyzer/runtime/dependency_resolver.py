"""Dependency Resolver for fetching third-party dependencies.

This module provides functions to resolve and fetch third-party dependencies
based on DependencySpec specifications.
"""

# Resolver catches fetcher exceptions so a single failed dependency doesn't abort the run.


from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Dict, Any

from depanalyzer.parsers.base import DependencySpec
from depanalyzer.parsers.registry import EcosystemRegistry

logger = logging.getLogger("depanalyzer.runtime.dependency_resolver")


def resolve_dependencies(
    deps: List[DependencySpec], cache_root: Path = Path(".depanalyzer_cache/deps")
) -> List[Dict[str, Any]]:
    """Resolve and fetch dependencies using appropriate fetchers.

    For each dependency specification:
    1. Find the appropriate DepFetcher for the ecosystem
    2. Call fetcher.fetch() to download/clone the dependency
    3. Return resolved dependency info with local path

    Args:
        deps: List of dependency specifications to resolve.
        cache_root: Root directory for caching downloaded dependencies.

    Returns:
        List[Dict[str, Any]]: List of resolved dependency information.
            Each dict contains:
            - name: Dependency name
            - version: Dependency version (if specified)
            - ecosystem: Ecosystem name
            - source: Local path to fetched dependency
            - spec: Original DependencySpec
            - success: Whether fetch was successful
            - error: Error message if fetch failed (optional)
    """
    registry = EcosystemRegistry.get_instance()
    resolved: List[Dict[str, Any]] = []

    # Ensure cache root exists
    cache_root.mkdir(parents=True, exist_ok=True)

    logger.info("Resolving %d dependencies", len(deps))

    for spec in deps:
        logger.info(
            "Resolving dependency: %s/%s (ecosystem=%s)",
            spec.name,
            spec.version or "latest",
            spec.ecosystem,
        )

        # Get the appropriate fetcher for this ecosystem
        fetcher_class = registry.get_dep_fetcher(spec.ecosystem)

        if not fetcher_class:
            logger.warning(
                "No fetcher registered for ecosystem '%s', skipping dependency: %s",
                spec.ecosystem,
                spec.name,
            )
            resolved.append(
                {
                    "name": spec.name,
                    "version": spec.version,
                    "ecosystem": spec.ecosystem,
                    "source": None,
                    "spec": spec,
                    "success": False,
                    "error": f"No fetcher for ecosystem '{spec.ecosystem}'",
                }
            )
            continue

        # Create fetcher instance and fetch dependency
        try:
            fetcher = fetcher_class(cache_root=cache_root)
            dep_path = fetcher.fetch(spec)

            if dep_path and dep_path.exists():
                logger.info(
                    "Successfully fetched dependency %s to: %s",
                    spec.name,
                    dep_path,
                )
                resolved.append(
                    {
                        "name": spec.name,
                        "version": spec.version,
                        "ecosystem": spec.ecosystem,
                        "source": str(dep_path),
                        "spec": spec,
                        "success": True,
                    }
                )
            else:
                logger.warning(
                    "Fetcher returned invalid path for %s: %s",
                    spec.name,
                    dep_path,
                )
                resolved.append(
                    {
                        "name": spec.name,
                        "version": spec.version,
                        "ecosystem": spec.ecosystem,
                        "source": None,
                        "spec": spec,
                        "success": False,
                        "error": "Fetcher returned invalid path",
                    }
                )

        except (OSError, ValueError) as e:
            logger.error(
                "Failed to fetch dependency %s: %s",
                spec.name,
                e,
                exc_info=True,
            )
            resolved.append(
                {
                    "name": spec.name,
                    "version": spec.version,
                    "ecosystem": spec.ecosystem,
                    "source": None,
                    "spec": spec,
                    "success": False,
                    "error": str(e),
                }
            )

    # Deduplicate successful resolutions by real path so that multiple
    # DependencySpec instances which resolve to the same local checkout
    # (for example different semver ranges of the same Hvigor/OHPM package)
    # do not spawn duplicate child transactions.
    unique_success: Dict[tuple, Dict[str, Any]] = {}
    deduped: List[Dict[str, Any]] = []

    for entry in resolved:
        if not entry.get("success"):
            deduped.append(entry)
            continue

        source = entry.get("source")
        # If there is no concrete source path, keep the entry as-is.
        if not source:
            deduped.append(entry)
            continue

        try:
            real_source = str(Path(source).resolve())
        except OSError:
            real_source = source

        key = (real_source,)
        if key in unique_success:
            prev_spec = unique_success[key].get("spec")
            curr_spec = entry.get("spec")
            logger.info(
                "Skipping duplicate resolved dependency %s/%s at %s "
                "(requested versions: %s, %s)",
                entry.get("ecosystem", "unknown"),
                entry.get("name", "unknown"),
                real_source,
                getattr(prev_spec, "version", None),
                getattr(curr_spec, "version", None),
            )
            continue

        unique_success[key] = entry
        deduped.append(entry)

    successful = sum(1 for r in deduped if r["success"])
    failed = len(deduped) - successful

    logger.info(
        "Dependency resolution completed: %d successful, %d failed",
        successful,
        failed,
    )

    return deduped
