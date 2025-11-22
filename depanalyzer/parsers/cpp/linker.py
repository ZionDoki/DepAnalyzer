"""CPP (CMake-based) ecosystem linker.

This linker currently focuses on enriching linkage information for
`link_libraries` edges between CMake targets and their dependencies.

It infers a `linkage_type` attribute for link edges based on the
target node's type / linkage_kind and marks these edges as
configuration-driven using `link_class=build_config`.
"""

from __future__ import annotations

import logging

from depanalyzer.graph import LinkClass
from depanalyzer.graph import EdgeKind, GraphManager, NodeType
from depanalyzer.parsers.base import BaseLinker

logger = logging.getLogger("depanalyzer.parsers.cpp.linker")


class CppLinker(BaseLinker):
    """Linker for C++/CMake ecosystem."""

    ECOSYSTEM = "cpp"

    def link(self) -> None:
        """Apply C++-specific linking logic to the current graph."""
        logger.info("CppLinker: starting linkage enrichment")

        cfg = getattr(self, "config", None)
        enable = True
        if cfg is not None:
            enable = bool(getattr(cfg, "enable_linkage_enrichment", True))

        if enable:
            self._enrich_linkage_edges()
        else:
            logger.info("CppLinker: linkage enrichment disabled by configuration")

        logger.info("CppLinker: linkage enrichment completed")

    def _enrich_linkage_edges(self) -> None:
        """Infer linkage_type for CMake link edges and tag them as build_config."""
        graph_manager: GraphManager = self.graph_manager
        backend = graph_manager.backend.native_graph 

        enriched_count = 0

        for _source, target, _key, attrs in backend.edges(data=True, keys=True):
            edge_kind = attrs.get("kind") or attrs.get("label") or attrs.get("type")

            if edge_kind not in (
                EdgeKind.LINKS.value,
                EdgeKind.LINK_LIBRARIES.value,
                "link_libraries",
            ):
                continue

            linkage_type = self._infer_linkage_type(graph_manager, target, attrs)
            if linkage_type == "unknown":
                continue

            # Update edge attributes in-place
            attrs["linkage_type"] = linkage_type
            attrs.setdefault("link_class", LinkClass.BUILD_CONFIG.value)
            enriched_count += 1

        logger.info("CppLinker: enriched %d linkage edges", enriched_count)

    @staticmethod
    def _infer_linkage_type(
        graph_manager: GraphManager,
        target: str,
        edge_attrs: dict,
    ) -> str:
        """Infer linkage type from target node attributes.

        Returns:
            str: Linkage type ('static', 'dynamic', 'module', 'unknown').
        """
        # Respect explicit annotation when present
        explicit = edge_attrs.get("linkage_type")
        if explicit:
            return explicit

        # Check over_approx flag - if over-approximated, mark as unknown
        if edge_attrs.get("over_approx", False):
            return "unknown"

        target_attrs = graph_manager.get_node(target)
        if not target_attrs:
            return "unknown"

        target_type = target_attrs.get("type")
        target_linkage_kind = target_attrs.get("linkage_kind")

        if (
            target_type == NodeType.SHARED_LIBRARY.value
            or target_linkage_kind == "shared"
        ):
            return "dynamic"
        if (
            target_type == NodeType.STATIC_LIBRARY.value
            or target_linkage_kind == "static"
        ):
            return "static"
        if target_type == NodeType.MODULE.value or target_linkage_kind == "module":
            return "module"
        if target_type in ["hap", "har", "hsp"]:
            # OpenHarmony packaging - typically dynamic at runtime
            return "dynamic"

        return "unknown"
