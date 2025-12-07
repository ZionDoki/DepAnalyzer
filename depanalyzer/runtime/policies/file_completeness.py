"""Policy that builds a fallback license-comparison tree."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Set

from depanalyzer.graph import (
    EdgeKind,
    EdgeSpec,
    GraphManager,
    LinkClass,
    NodeSpec,
    NodeType,
    canonicalize_normalized_id,
)
from depanalyzer.runtime.context import JoinContext
from depanalyzer.runtime.graph_config import FallbackConfig
from depanalyzer.runtime.policies.protocols import JoinPolicy

logger = logging.getLogger("depanalyzer.runtime.policies.file_completeness")


class FileCompletenessJoinPolicy(JoinPolicy):
    """Connect unparsed/isolated files into a flat tree for license fallback."""

    def __init__(self, config: FallbackConfig) -> None:
        self._config = config

    def join(self, ctx: JoinContext) -> None:
        if not self._config.enabled:
            return

        graph = ctx.graph
        workspace = ctx.workspace
        if graph is None or workspace is None or workspace.root_path is None:
            logger.debug("FileCompletenessJoinPolicy: missing graph or workspace")
            return

        raw_root_id = self._config.root_id or "fallback:license_scan"
        normalized_root_id = raw_root_id
        if not str(raw_root_id).startswith("//"):
            normalized_root_id = f"//{str(raw_root_id).lstrip('/')}"
        normalized_root_id = normalized_root_id.replace("\\", "/")
        fallback_root_id = canonicalize_normalized_id(normalized_root_id)

        if not graph.has_node(fallback_root_id):
            graph.add_node_spec(
                NodeSpec(
                    id=fallback_root_id,
                    type=NodeType.BUILD_CONFIG,
                    label=fallback_root_id,
                    parser_name="fallback",
                    confidence=0.8,
                    attrs={
                        "origin": "synthetic",
                        "provenance": "fallback_license_tree",
                    },
                )
            )

        known_paths = _collect_known_paths(graph)
        new_files = _find_untracked_files(workspace.root_path, known_paths)

        for file_path in new_files:
            node_id = _normalize_path(graph, file_path)
            if not graph.has_node(node_id):
                graph.add_node_spec(
                    NodeSpec(
                        id=node_id,
                        type=NodeType.CODE,
                        src_path=str(file_path),
                        name=file_path.name,
                        parser_name="fallback",
                        confidence=0.5,
                        provisional=True,
                        attrs={
                            "origin": "fallback",
                            "provenance": "fallback_license_tree",
                        },
                    )
                )

            graph.add_edge_spec(
                EdgeSpec(
                    source=fallback_root_id,
                    target=node_id,
                    kind=EdgeKind.CONTAINS,
                    parser_name="fallback",
                    confidence=0.5,
                    attrs={"link_class": LinkClass.BUILD_CONFIG.value},
                )
            )

        if self._config.include_isolated_nodes:
            _connect_isolated_nodes(graph, fallback_root_id)

        logger.info(
            "FileCompletenessJoinPolicy: attached %d files and isolated nodes",
            len(new_files),
        )

        # Note: We no longer skip cycle condensation for fallback trees.
        # The flat tree itself won't have cycles, but the original graph
        # may contain cycles that need to be broken for DAG consumers.


def _collect_known_paths(graph: GraphManager) -> Set[Path]:
    paths: Set[Path] = set()
    for _, attrs in graph.nodes():
        for key in ("src_path", "path"):
            val = attrs.get(key)
            if not val:
                continue
            try:
                paths.add(Path(val).resolve())
            except (OSError, ValueError):
                continue
    return paths


# Directories to skip during fallback file scanning
_IGNORED_DIRS = frozenset({
    ".git", ".svn", ".hg",
    "node_modules", ".pnpm",
    "target", "build", "dist", "out",
    ".gradle", ".hvigor",
    "__pycache__", ".pytest_cache", ".mypy_cache",
    ".venv", "venv", "env",
    ".idea", ".vscode",
})


def _find_untracked_files(root: Path, known_paths: Set[Path]) -> Set[Path]:
    """Find files not already tracked in the graph.

    Uses manual traversal to skip ignored directories and avoid symlink loops.
    """
    files: Set[Path] = set()
    seen_dirs: Set[Path] = set()  # Track resolved dirs to detect symlink loops

    def _walk(directory: Path) -> None:
        try:
            resolved_dir = directory.resolve()
        except (OSError, ValueError):
            return

        # Symlink loop detection
        if resolved_dir in seen_dirs:
            return
        seen_dirs.add(resolved_dir)

        try:
            entries = list(directory.iterdir())
        except (OSError, PermissionError):
            return

        for entry in entries:
            try:
                if entry.is_dir():
                    # Skip ignored directories
                    if entry.name in _IGNORED_DIRS:
                        continue
                    _walk(entry)
                elif entry.is_file():
                    try:
                        resolved = entry.resolve()
                    except (OSError, ValueError):
                        continue
                    if resolved not in known_paths:
                        files.add(resolved)
            except (OSError, ValueError):
                continue

    try:
        _walk(root)
    except (OSError, ValueError) as exc:
        logger.warning("FileCompletenessJoinPolicy: failed scanning workspace: %s", exc)

    return files


def _normalize_path(graph: GraphManager, path: Path) -> str:
    try:
        if graph.root_path:
            return graph.normalize_path(path)
    except Exception:
        pass
    return str(path)


def _connect_isolated_nodes(graph: GraphManager, root_id: str) -> None:
    for node_id, _ in graph.nodes():
        if node_id == root_id:
            continue
        if graph.in_degree(node_id) == 0 and graph.out_degree(node_id) == 0:
            graph.add_edge(
                root_id,
                node_id,
                edge_kind=EdgeKind.CONTAINS.value,
                parser_name="fallback",
                link_class=LinkClass.BUILD_CONFIG.value,
                confidence=0.4,
            )


FallbackJoinStrategy = FileCompletenessJoinPolicy

__all__ = ["FileCompletenessJoinPolicy", "FallbackJoinStrategy"]
