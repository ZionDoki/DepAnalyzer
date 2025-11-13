"""Dependency parser for resolving and fetching third-party dependencies.

Parses dependency declarations and uses fetchers to download dependencies.
Creates ExternalDep nodes in the transaction graph.
"""

import logging
from pathlib import Path
from typing import Dict, List

from core.dependency import DependencySpec
from graph.manager import GraphManager, NodeType
from parsers.base import BaseParser
from parsers.deps.fetchers.git import GitFetcher
from parsers.deps.fetchers.hvigor import HvigorFetcher
from runtime.eventbus import Event, EventBus, EventType

logger = logging.getLogger("depanalyzer.parsers.deps.parser")


class DepsParser(BaseParser):
    """Parser for dependency resolution and fetching.

    Consumes DependencySpec objects (discovered by other parsers)
    and uses fetchers to download and cache dependencies.
    """

    NAME = "deps"

    def __init__(
        self,
        workspace_root: Path,
        graph_manager: GraphManager,
        eventbus: EventBus,
        cache_root: Path,
    ) -> None:
        """Initialize dependency parser.

        Args:
            workspace_root: Workspace root path.
            graph_manager: Transaction graph manager.
            eventbus: Event bus for publishing parse events.
            cache_root: Root directory for dependency cache.
        """
        super().__init__(workspace_root, graph_manager, eventbus)
        self.cache_root = cache_root

        # Initialize fetchers
        self.fetchers = [
            HvigorFetcher(),
            GitFetcher(),
        ]

        logger.info("DepsParser initialized with %d fetchers", len(self.fetchers))

    def parse(self, target_path: Path) -> None:
        """Parse a dependency declaration file.

        This is typically called for lockfiles or explicit dependency configs.
        For most cases, use resolve_dependency() directly with DependencySpec.

        Args:
            target_path: Path to dependency declaration file.
        """
        logger.debug("Parsing dependency file: %s", target_path)
        # Implementation would parse specific file formats
        # For now, this is a placeholder
        pass

    def resolve_dependency(self, spec: DependencySpec) -> bool:
        """Resolve and fetch a dependency.

        Args:
            spec: Dependency specification to resolve.

        Returns:
            bool: True if dependency was successfully fetched.
        """
        logger.info("Resolving dependency: %s@%s", spec.name, spec.version)

        # Find appropriate fetcher
        fetcher = self._get_fetcher(spec)
        if not fetcher:
            logger.warning(
                "No fetcher available for %s (type: %s)",
                spec.name,
                spec.dependency_type.name,
            )
            self._create_unresolved_node(spec)
            return False

        # Fetch dependency
        result = fetcher.fetch(spec, self.cache_root)

        if not result.success:
            logger.error(
                "Failed to fetch %s: %s", spec.name, result.error
            )
            self._create_unresolved_node(spec, error=result.error)
            return False

        # Create node in graph
        self._create_dependency_node(spec, result)

        # Publish event
        event = Event(
            event_type=EventType.EXTERNAL_DEP_RESOLVED,
            source=self.NAME,
            data={
                "spec": spec,
                "result": result,
                "local_path": str(result.local_path) if result.local_path else None,
                "license_only": result.license_only,
            },
        )
        self.publish_parse_event(event)

        logger.info(
            "Successfully resolved %s@%s (license_only=%s)",
            spec.name,
            result.metadata.get("resolved_version", spec.version),
            result.license_only,
        )
        return True

    def _get_fetcher(self, spec: DependencySpec):
        """Find appropriate fetcher for dependency spec.

        Args:
            spec: Dependency specification.

        Returns:
            BaseFetcher or None: Fetcher that supports this spec type.
        """
        for fetcher in self.fetchers:
            if fetcher.supports(spec):
                return fetcher
        return None

    def _create_dependency_node(self, spec: DependencySpec, result) -> None:
        """Create ExternalDep node in graph for resolved dependency.

        Args:
            spec: Dependency specification.
            result: Fetch result.
        """
        node_id = f"//external:{spec.name}@{spec.version}"

        self.add_node(
            node_id,
            NodeType.EXTERNAL_DEP,
            name=spec.name,
            version=result.metadata.get("resolved_version", spec.version),
            dependency_type=spec.dependency_type.name,
            local_path=str(result.local_path) if result.local_path else None,
            license=result.metadata.get("license"),
            license_only=result.license_only,
            parser_name=self.NAME,
            confidence=1.0 if not result.license_only else 0.5,
        )

    def _create_unresolved_node(
        self, spec: DependencySpec, error: Exception = None
    ) -> None:
        """Create unresolved ExternalDep node with over-approximation flag.

        Args:
            spec: Dependency specification.
            error: Optional error that caused resolution failure.
        """
        node_id = f"//external:{spec.name}@{spec.version}"

        self.add_node(
            node_id,
            NodeType.EXTERNAL_DEP,
            name=spec.name,
            version=spec.version,
            dependency_type=spec.dependency_type.name,
            resolved=False,
            parser_name=self.NAME,
            confidence=0.0,
            over_approx=True,
            evidence=[f"resolution_failed: {error}" if error else "no_fetcher"],
        )
