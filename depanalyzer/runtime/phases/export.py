"""
EXPORT phase implementation.

This phase exports the graph to disk in a serialized format.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from networkx import MultiDiGraph
from networkx.readwrite import node_link_data

from depanalyzer.graph.io import break_cycles
from depanalyzer.graph import (
    canonicalize_edge,
    canonicalize_node,
    canonicalize_normalized_id,
)
from depanalyzer.runtime.lifecycle import LifecyclePhase
from depanalyzer.runtime.phases.base import BasePhase
from depanalyzer.runtime.context import ExportContext

logger = logging.getLogger("depanalyzer.transaction.phase.export")

_SAFE_EXCEPTIONS = (RuntimeError, ValueError, TypeError, AttributeError, KeyError,
                    IndexError, OSError, ImportError, LookupError)


def _make_json_serializable(obj: Any) -> Any:
    """
    Convert objects to JSON-serializable formats.

    Args:
        obj: Object to convert

    Returns:
        JSON-serializable version of the object
    """
    if isinstance(obj, Path):
        return str(obj)
    elif isinstance(obj, dict):
        return {k: _make_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_make_json_serializable(item) for item in obj]
    elif isinstance(obj, set):
        return list(_make_json_serializable(item) for item in obj)
    elif hasattr(obj, '__dict__'):
        # For custom objects, try to convert to dict
        return str(obj)
    else:
        return obj


class ExportPhase(BasePhase):
    """
    EXPORT phase: Export graph to disk.

    This phase:
    1. Canonicalizes graph nodes and edges
    2. Serializes graph to JSON
    3. Writes graph to cache directory

    Critical: False - Export failures shouldn't rollback analysis.
    """

    IS_CRITICAL = False  # Non-critical: export is optional

    def execute(self, context: ExportContext) -> None:
        """Execute EXPORT phase logic."""
        if not self.state.graph_manager:
            logger.warning("GraphManager not initialized, skipping export")
            return

        serialized = self._serialize_graph_data()
        if not serialized:
            return

        graph_data, node_count, edge_count = serialized

        cache_path = self._write_cache_export(graph_data, node_count, edge_count)
        if cache_path:
            self.state.update_phase_result(
                LifecyclePhase.EXPORT,
                "graph_cache_path",
                cache_path,
            )

        if context.output_path is not None:
            self._write_requested_export(
                context.output_path,
                graph_data,
                node_count,
                edge_count,
            )

    def _serialize_graph_data(self) -> Optional[tuple[Dict[str, Any], int, int]]:
        """
        Build canonicalized graph data for export.
        """
        try:
            native_graph = self.state.graph_manager.backend.native_graph

            # Canonicalize nodes and edges
            export_graph = MultiDiGraph()
            node_id_map = {}

            workspace_root = None
            try:
                workspace_root = (
                    self.state.workspace.root_path if self.state.workspace else None
                )
            except _SAFE_EXCEPTIONS:
                workspace_root = None

            # Canonicalize nodes
            for raw_id, attrs in native_graph.nodes(data=True):
                canonical_id, canonical_attrs = canonicalize_node(
                    raw_id,
                    attrs,
                    workspace_root,
                )
                node_id_map[str(raw_id)] = canonical_id
                export_graph.add_node(canonical_id, **canonical_attrs)

            # Canonicalize edges
            for u, v, _key, attrs in native_graph.edges(data=True, keys=True):
                source_id = node_id_map.get(str(u), str(u))
                target_id = node_id_map.get(str(v), str(v))
                canonical_attrs = canonicalize_edge(attrs or {})
                export_graph.add_edge(source_id, target_id, **canonical_attrs)

            # Graph metadata may influence export behavior (e.g., skip cycle condensation).
            metadata = self.state.graph_manager.metadata or {}

            # Remove cycles to keep downstream DAG consumers fast/safe unless
            # explicitly disabled (e.g., fallback flat tree).
            skip_cycles = metadata.get("skip_cycle_condensation", False)
            if skip_cycles:
                acyclic_graph = export_graph
                removed_edges = 0
            else:
                acyclic_graph, removed_edges = break_cycles(export_graph)

            # Add metadata (make JSON-serializable)
            if removed_edges:
                logger.info(
                    "Removed %d cycle edge(s) to produce DAG-safe export",
                    removed_edges,
                )
                metadata = dict(metadata)
                sanitization = dict(metadata.get("sanitization", {}))
                sanitization["cycle_edges_removed"] = int(removed_edges)
                metadata["sanitization"] = sanitization
            # Canonicalize dead_nodes metadata with exported node IDs if present
            if metadata and "dead_nodes" in metadata:
                dead_nodes = metadata.get("dead_nodes") or []
                canonical_dead: list[str] = []
                for nid in dead_nodes:
                    cid = node_id_map.get(str(nid))
                    if cid:
                        canonical_dead.append(cid)
                        continue
                    try:
                        if isinstance(nid, str):
                            if nid.startswith("//"):
                                canonical_dead.append(canonicalize_normalized_id(nid))
                                continue
                            if nid.startswith(("ext_lib:", "module:")):
                                canonical_dead.append(f"//{nid}")
                                continue
                    except Exception:
                        pass
                metadata["dead_nodes"] = sorted(set(canonical_dead))

            if (
                "root_path" not in metadata
                and self.state.graph_manager.root_path is not None
            ):
                metadata["root_path"] = self.state.graph_manager.root_path
            if (
                "path_namespace" not in metadata
                and self.state.graph_manager.path_namespace
            ):
                metadata["path_namespace"] = self.state.graph_manager.path_namespace
            serializable_metadata = _make_json_serializable(metadata)
            serializable_metadata.setdefault(
                "node_count", acyclic_graph.number_of_nodes()
            )
            serializable_metadata.setdefault(
                "edge_count", acyclic_graph.number_of_edges()
            )

            # Attach metadata to the graph attributes so node_link_data
            # produces a NetworkX-native structure without extra keys.
            acyclic_graph.graph.clear()
            acyclic_graph.graph.update(
                {
                    "graph_id": self.state.graph_id,
                    "source": self.state.source,
                    **serializable_metadata,
                }
            )

            # Serialize to node-link format (NetworkX default uses `links` for edges)
            data = node_link_data(acyclic_graph, edges="edges")
            data["metadata"] = serializable_metadata
            data["graph_id"] = self.state.graph_id
            data["source"] = self.state.source

            return data, export_graph.number_of_nodes(), export_graph.number_of_edges()

        except _SAFE_EXCEPTIONS as e:
            logger.error("Failed to serialize graph for export: %s", e, exc_info=True)
            return None

    def _write_cache_export(
        self,
        graph_data: Dict[str, Any],
        node_count: int,
        edge_count: int,
    ) -> Optional[Path]:
        """
        Write the serialized graph to the configured cache directory.
        """
        if not self.state.graph_cache_root or not self.state.graph_id:
            logger.info("Graph caching disabled (no cache_root or graph_id)")
            return None

        try:
            cache_dir = Path(self.state.graph_cache_root)
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = cache_dir / f"{self.state.graph_id}.json"
            self._write_graph_file(cache_file, graph_data)
            logger.info(
                "Graph exported to: %s (%d nodes, %d edges)",
                cache_file,
                node_count,
                edge_count,
            )
            return cache_file
        except _SAFE_EXCEPTIONS as e:
            logger.error("Failed to write graph cache: %s", e, exc_info=True)
            return None

    def _write_requested_export(
        self,
        output_path: Path,
        graph_data: Dict[str, Any],
        node_count: int,
        edge_count: int,
    ) -> Optional[Path]:
        """
        Write the serialized graph to the caller-provided path.
        """
        try:
            written_path = self._write_graph_file(output_path, graph_data)
            logger.info(
                "Graph exported to requested path: %s (%d nodes, %d edges)",
                written_path,
                node_count,
                edge_count,
            )
            return written_path
        except _SAFE_EXCEPTIONS as e:
            logger.error("Failed to export graph to %s: %s", output_path, e, exc_info=True)
            return None

    def _write_graph_file(self, target_path: Path, graph_data: Dict[str, Any]) -> Path:
        """
        Write graph data to disk.
        """
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with target_path.open("w", encoding="utf-8") as handle:
            json.dump(graph_data, handle, indent=2, default=str)
        return target_path
