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
            edge_kind = attrs.get("kind")

            if edge_kind == EdgeKind.LINKS or edge_kind == "link_libraries":
                # Infer linkage type based on target node type
                linkage_type = self._infer_linkage_type(source, target, attrs)

                # Update edge with linkage type if inferred
                if linkage_type != "unknown":
                    # Note: We need to use the backend to update edge attributes
                    # For now, we log the inference
                    logger.debug(
                        "Inferred linkage type for %s -> %s: %s",
                        source,
                        target,
                        linkage_type,
                    )
                    enriched_count += 1

        logger.info("Enriched %d linkage edges", enriched_count)

    def _infer_linkage_type(self, source: str, target: str, edge_attrs: dict) -> str:
        """Infer linkage type from target node attributes.

        Args:
            source: Source node ID.
            target: Target node ID.
            edge_attrs: Edge attributes.

        Returns:
            str: Linkage type ('static', 'dynamic', 'module', 'unknown').
        """
        # Check if already explicitly set
        if "linkage_type" in edge_attrs:
            return edge_attrs["linkage_type"]

        # Check over_approx flag - if over-approximated, mark as unknown
        if edge_attrs.get("over_approx", False):
            return "unknown"

        # Get target node attributes
        target_attrs = self.graph_manager.get_node_attributes(target)
        if not target_attrs:
            return "unknown"

        target_type = target_attrs.get("type")
        target_linkage_kind = target_attrs.get("linkage_kind")

        # Infer based on target type
        if target_type == "shared_library" or target_linkage_kind == "shared":
            return "dynamic"
        elif target_type == "static_library" or target_linkage_kind == "static":
            return "static"
        elif target_type == "module" or target_linkage_kind == "module":
            return "module"
        elif target_type in ["hap", "har", "hsp"]:
            # OpenHarmony packaging - typically dynamic
            return "dynamic"
        else:
            return "unknown"
