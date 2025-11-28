"""Policy that attaches discovered license files to graph roots."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Set

from depanalyzer.graph import EdgeKind, EdgeSpec, GraphManager, LinkClass, NodeSpec, NodeType
from depanalyzer.runtime.context import JoinContext
from depanalyzer.runtime.graph_config import LicenseLinkConfig
from depanalyzer.runtime.policies.protocols import JoinPolicy

logger = logging.getLogger("depanalyzer.runtime.policies.license_attach")


class LicenseAttachmentPolicy(JoinPolicy):
    """Discover license files and attach them to non-isolated root nodes."""

    def __init__(self, config: LicenseLinkConfig) -> None:
        """Initialize license attachment policy.

        Args:
            config: LicenseLinkConfig controlling discovery and linking.
        """
        self._config = config

    def join(self, ctx: JoinContext) -> None:
        """Discover license files and attach them to graph roots."""
        if not self._config.enabled:
            return

        graph = ctx.graph
        workspace = ctx.workspace
        if graph is None or workspace is None or workspace.root_path is None:
            logger.debug("LicenseAttachmentPolicy: missing graph or workspace")
            return

        license_paths = self._find_license_files(workspace.root_path)
        if not license_paths:
            logger.info("LicenseAttachmentPolicy: no license files found under workspace")
            return

        license_nodes = self._ensure_license_nodes(graph, workspace.root_path, license_paths)
        root_nodes = self._find_root_nodes(graph)
        if not root_nodes:
            logger.debug("LicenseAttachmentPolicy: no root nodes to attach licenses to")
            return

        edges_added = 0
        backend = graph.backend.native_graph

        for license_id in license_nodes:
            for root_id in root_nodes:
                if backend.has_edge(root_id, license_id):
                    continue
                graph.add_edge_spec(
                    EdgeSpec(
                        source=root_id,
                        target=license_id,
                        kind=EdgeKind.CONTAINS,
                        parser_name="license_policy",
                        attrs={
                            "link_class": LinkClass.BUILD_CONFIG.value,
                            "provenance": "license_attachment",
                        },
                    )
                )
                edges_added += 1

        logger.info(
            "LicenseAttachmentPolicy: attached %d license node(s) to %d root node(s) (%d edges)",
            len(license_nodes),
            len(root_nodes),
            edges_added,
        )

    def _find_license_files(self, root: Path) -> Set[Path]:
        paths: Set[Path] = set()
        patterns = self._config.file_patterns or []
        for pattern in patterns:
            try:
                for path in root.rglob(pattern):
                    if path.is_file():
                        paths.add(path.resolve())
            except (OSError, ValueError) as exc:
                logger.warning(
                    "LicenseAttachmentPolicy: failed scanning pattern %s: %s",
                    pattern,
                    exc,
                )
        return paths

    def _ensure_license_nodes(
        self,
        graph: GraphManager,
        workspace_root: Path,
        paths: Iterable[Path],
    ) -> Set[str]:
        node_ids: Set[str] = set()
        for path in paths:
            node_id = self._normalize_path(graph, path)
            existing = graph.get_node(node_id)

            if existing is None:
                label = self._relative_label(workspace_root, path)
                node_spec = NodeSpec(
                    id=node_id,
                    type=NodeType.LICENSE,
                    label=label,
                    src_path=str(path),
                    name=path.name,
                    parser_name="license_policy",
                    confidence=0.9,
                    attrs={
                        "origin": "repository",
                        "provenance": "license_discovery",
                    },
                )
                graph.add_node_spec(node_spec)
            else:
                if existing.get("type") != NodeType.LICENSE.value:
                    graph.update_node_attribute(node_id, "type", NodeType.LICENSE.value)
                if not existing.get("src_path"):
                    graph.update_node_attribute(node_id, "src_path", str(path))
                if not existing.get("parser_name"):
                    graph.update_node_attribute(node_id, "parser_name", "license_policy")

            node_ids.add(node_id)
        return node_ids

    def _find_root_nodes(self, graph: GraphManager) -> list[str]:
        backend = graph.backend.native_graph
        roots: list[str] = []
        for node_id in backend.nodes():
            if backend.in_degree(node_id) == 0 and backend.out_degree(node_id) > 0:
                roots.append(str(node_id))
        return roots

    def _normalize_path(self, graph: GraphManager, path: Path) -> str:
        try:
            if graph.root_path:
                return graph.normalize_path(path)
        except Exception:
            logger.debug("LicenseAttachmentPolicy: failed normalizing %s", path)
        try:
            return str(path.resolve())
        except (OSError, ValueError):
            return str(path)

    @staticmethod
    def _relative_label(workspace_root: Path, path: Path) -> str:
        try:
            return str(path.relative_to(workspace_root))
        except ValueError:
            return str(path)

