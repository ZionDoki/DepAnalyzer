"""Linkage enrichment hook for enhancing link edges with OHOS_STL info."""

import logging

from depanalyzer.graph.manager import EdgeKind, GraphManager
from depanalyzer.runtime.eventbus import EventBus

logger = logging.getLogger("depanalyzer.hooks.linkage_enrichment")


class LinkageEnrichment:
    """Hook for enriching linkage information.

    Merges CMake semantic linkage info with OHOS_STL data to
    determine static/shared/module linkage types.
    """

    def __init__(self, graph_manager: GraphManager, eventbus: EventBus) -> None:
        """Initialize linkage enrichment hook.

        Args:
            graph_manager: Transaction graph manager.
            eventbus: Event bus.
        """
        self.graph_manager = graph_manager
        self.eventbus = eventbus
        logger.info("LinkageEnrichment hook initialized")

    def execute(self) -> None:
        """Execute hook to enrich linkage edges."""
        logger.info("Executing LinkageEnrichment hook")

        # Find all link edges and enrich with linkage type
        enriched_count = 0
        for source, target, key, attrs in self.graph_manager.edges():
            if attrs.get("kind") == EdgeKind.LINKS:
                # TODO: Implement linkage type inference
                enriched_count += 1

        logger.info("Enriched %d linkage edges", enriched_count)
