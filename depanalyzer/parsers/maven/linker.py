"""Linker for Maven ecosystem."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Tuple

from depanalyzer.graph import LinkClass
from depanalyzer.graph import EdgeKind, GraphManager, NodeType
from depanalyzer.parsers.base import BaseLinker

logger = logging.getLogger("depanalyzer.parsers.maven.linker")


class MavenLinker(BaseLinker):
    """Attach Java code nodes to Maven modules and artifacts."""

    ECOSYSTEM = "maven"

    def link(self) -> None:
        logger.info("MavenLinker: starting linking")
        modules = self._collect_modules()
        if not modules:
            logger.info("MavenLinker: no modules found")
            return

        code_nodes = self._collect_code_nodes()

        edges_added = 0
        for code_id, code_path in code_nodes:
            for module_id, module_root, source_roots in modules:
                if self._belongs_to_module(code_path, module_root, source_roots):
                    self.graph_manager.add_edge(
                        module_id,
                        code_id,
                        edge_kind=EdgeKind.SOURCES.value,
                        parser_name="maven_linker",
                        link_class=LinkClass.BUILD_CONFIG.value,
                        derived_from="maven_module_membership",
                    )
                    edges_added += 1

        logger.info("MavenLinker: attached %d module->code edges", edges_added)

    def _collect_modules(self) -> List[Tuple[str, Path, List[Path]]]:
        modules: List[Tuple[str, Path, List[Path]]] = []
        for node_id, attrs in self.graph_manager.nodes():
            if attrs.get("type") != NodeType.MODULE.value:
                continue
            if attrs.get("parser_name") != "maven":
                continue

            src = attrs.get("src_path")
            if not src:
                continue
            try:
                module_root = Path(src).resolve()
            except OSError:
                continue

            source_roots = []
            raw_roots = attrs.get("source_roots") or []
            if isinstance(raw_roots, list):
                for root in raw_roots:
                    try:
                        source_roots.append(Path(root).resolve())
                    except OSError:
                        continue

            modules.append((node_id, module_root, source_roots))
        return modules

    def _collect_code_nodes(self) -> List[Tuple[str, Path]]:
        codes: List[Tuple[str, Path]] = []
        for node_id, attrs in self.graph_manager.nodes():
            if attrs.get("type") != NodeType.CODE.value:
                continue
            parser_name = str(attrs.get("parser_name") or "")
            if "maven" not in parser_name and "java" not in parser_name:
                continue
            src_path = attrs.get("src_path") or attrs.get("path")
            if not src_path:
                continue
            try:
                codes.append((node_id, Path(src_path).resolve()))
            except OSError:
                continue
        return codes

    def _belongs_to_module(
        self, code_path: Path, module_root: Path, source_roots: List[Path]
    ) -> bool:
        try:
            code_path.resolve().relative_to(module_root)
            if not source_roots:
                return True
            for root in source_roots:
                try:
                    code_path.resolve().relative_to(root)
                    return True
                except ValueError:
                    continue
        except ValueError:
            return False
        return False


__all__ = ["MavenLinker"]
