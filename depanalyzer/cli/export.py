"""Export command implementation."""

# Export command intentionally guards against unexpected exceptions to report failures cleanly.


import json
import logging
from pathlib import Path
from typing import List

from networkx.exception import NetworkXError
from networkx.readwrite import node_link_graph

from depanalyzer.graph import GlobalDAG, GraphRegistry

logger = logging.getLogger("depanalyzer.cli.export")


def export_command(args) -> int:
    """Execute export command.

    Args:
        args: Parsed command-line arguments containing:
            - graph_id: Graph identifier to export
            - output: Output file path
            - format: Export format (json, gml, dot, etc.)
            - with_deps: Include dependency graphs (optional)

    Returns:
        int: Exit code (0 for success, non-zero for failure).
    """
    logger.info("=== Depanalyzer Export ===")

    try:
        graph_id = args.graph_id
        output_path = Path(args.output)
        export_format = getattr(args, "format", "json")
        with_deps = getattr(args, "with_deps", False)
        work_dir_arg = getattr(args, "work_dir", None)

        # Determine where graphs are stored on disk
        if work_dir_arg:
            work_dir = Path(work_dir_arg).expanduser().resolve()
            graphs_root = work_dir / "graphs"
            logger.info("Using work directory for graphs: %s", graphs_root)
        else:
            graphs_root = Path(".depanalyzer_cache/graphs")

        logger.info("Exporting graph: %s", graph_id)
        logger.info("Output path: %s", output_path)
        logger.info("Format: %s", export_format)
        logger.info("Include dependencies: %s", with_deps)

        # Get graph registry
        registry = GraphRegistry.get_instance(cache_root=graphs_root)

        # Get main graph cache path
        cache_path = registry.get_cache_path(graph_id)
        if not cache_path:
            logger.error("Graph not found: %s", graph_id)
            return 1

        # Load main graph
        graphs_to_export = []
        graphs_to_export.append(
            {
                "graph_id": graph_id,
                "cache_path": cache_path,
                "is_main": True,
            }
        )

        # If --with-deps is enabled, load all dependency graphs
        if with_deps:
            global_dag = GlobalDAG.get_instance(cache_dir=graphs_root)

            # Get dependency graphs in topological order
            try:
                dep_graph_ids = global_dag.get_transitive_dependencies(graph_id)
                logger.info("Found %d dependency graphs", len(dep_graph_ids))

                for dep_id in dep_graph_ids:
                    dep_cache_path = registry.get_cache_path(dep_id)
                    if dep_cache_path:
                        graphs_to_export.append(
                            {
                                "graph_id": dep_id,
                                "cache_path": dep_cache_path,
                                "is_main": False,
                            }
                        )
                    else:
                        logger.warning("Dependency graph not found: %s", dep_id)

            except (OSError, RuntimeError, TimeoutError, ValueError) as err:
                logger.warning("Failed to get dependency graphs: %s", err)

        # Determine path to GlobalDAG (if present)
        global_dag_path = graphs_root / "global_dag.json"

        # Load and combine graphs
        if export_format == "json":
            result = _export_json(graphs_to_export, output_path, global_dag_path)
        elif export_format == "asset_artifact":
            # Special format for asset->artifact mapping
            result = _export_asset_artifact_mapping(graphs_to_export, output_path)
        else:
            logger.error("Unsupported export format: %s", export_format)
            return 1

        if result:
            logger.info("Export successful: %s", output_path)
            return 0
        else:
            return 1

    except (
        AttributeError,
        json.JSONDecodeError,
        OSError,
        RuntimeError,
        TimeoutError,
        ValueError,
    ) as err:
        logger.error("Export failed: %s", err, exc_info=True)
        return 1


def _export_asset_artifact_mapping(graphs: List[dict], output_path: Path) -> bool:
    """Export asset->artifact mapping view.

    Args:
        graphs: List of graph info dicts with graph_id, cache_path, is_main.
        output_path: Output file path.

    Returns:
        bool: True if successful, False otherwise.
    """
    try:
        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        all_mappings = []

        # Process each graph
        for graph_info in graphs:
            cache_path = Path(graph_info["cache_path"])

            with open(cache_path, "r", encoding="utf-8") as f:
                graph_data = json.load(f)

            # Reconstruct NetworkX graph
            nx_graph = node_link_graph(graph_data["graph"])

            # Extract asset and artifact nodes
            asset_nodes = []
            artifact_nodes = []

            for node_id, attrs in nx_graph.nodes(data=True):
                node_type = attrs.get("type")

                # Asset types
                if node_type in [
                    "asset",
                    "code",
                    "header",
                    "project_header",
                    "system_header",
                    "source",
                ]:
                    asset_nodes.append(
                        {
                            "id": node_id,
                            "type": node_type,
                            "name": attrs.get("name", node_id),
                            "path": attrs.get("path") or attrs.get("src_path"),
                        }
                    )

                # Artifact types
                elif node_type in [
                    "shared_library",
                    "static_library",
                    "executable",
                    "module",
                    "hap",
                    "har",
                    "hsp",
                    "target",
                ]:
                    artifact_nodes.append(
                        {
                            "id": node_id,
                            "type": node_type,
                            "name": attrs.get("name", node_id),
                            "linkage_kind": attrs.get("linkage_kind"),
                        }
                    )

            # Build asset->artifact mapping
            mapping = {}
            for asset in asset_nodes:
                asset_id = asset["id"]
                # Find all artifacts this asset contributes to
                artifacts = []

                # BFS to find reachable artifacts
                visited = set()
                queue = [asset_id]

                while queue:
                    current = queue.pop(0)
                    if current in visited:
                        continue
                    visited.add(current)

                    # Check if current is an artifact
                    current_attrs = nx_graph.nodes.get(current)
                    if current_attrs:
                        current_type = current_attrs.get("type")
                        if current_type in [
                            "shared_library",
                            "static_library",
                            "executable",
                            "module",
                            "hap",
                            "har",
                            "hsp",
                            "target",
                        ]:
                            artifacts.append(
                                {
                                    "id": current,
                                    "type": current_type,
                                    "name": current_attrs.get("name", current),
                                }
                            )

                    # Add successors to queue
                    for successor in nx_graph.successors(current):
                        if successor not in visited:
                            queue.append(successor)

                if artifacts:
                    mapping[asset_id] = {
                        "asset": asset,
                        "artifacts": artifacts,
                    }

            all_mappings.append(
                {
                    "graph_id": graph_data["metadata"]["graph_id"],
                    "is_main": graph_info["is_main"],
                    "asset_count": len(asset_nodes),
                    "artifact_count": len(artifact_nodes),
                    "mappings": mapping,
                }
            )

        # Combine into single output
        output_data = {
            "summary": {
                "total_graphs": len(all_mappings),
                "total_assets": sum(m["asset_count"] for m in all_mappings),
                "total_artifacts": sum(m["artifact_count"] for m in all_mappings),
            },
            "mappings": all_mappings,
        }

        # Write to file
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2)

        logger.info(
            "Exported asset->artifact mappings: %d assets -> %d artifacts",
            output_data["summary"]["total_assets"],
            output_data["summary"]["total_artifacts"],
        )
        return True

    except (OSError, json.JSONDecodeError, NetworkXError, ValueError) as err:
        logger.error("Failed to export asset->artifact mapping: %s", err, exc_info=True)
        return False


def _export_json(
    graphs: List[dict],
    output_path: Path,
    global_dag_path: Path | None = None,
) -> bool:
    """Export graphs to JSON format.

    Args:
        graphs: List of graph info dicts with graph_id, cache_path, is_main.
        output_path: Output file path.

    Returns:
        bool: True if successful, False otherwise.
    """
    try:
        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        all_graphs = []

        # Load each graph from cache
        for graph_info in graphs:
            cache_path = Path(graph_info["cache_path"])

            with open(cache_path, "r", encoding="utf-8") as f:
                graph_data = json.load(f)

            # Add metadata about whether this is the main graph
            graph_data["is_main"] = graph_info["is_main"]

            all_graphs.append(graph_data)

        # Combine into single output
        output_data = {
            "graphs": all_graphs,
            "graph_count": len(all_graphs),
        }

        # Optionally embed GlobalDAG information for cross-graph dependencies
        if global_dag_path is not None and global_dag_path.exists():
            try:
                with open(global_dag_path, "r", encoding="utf-8") as f:
                    dag_data = json.load(f)
                output_data["global_dag"] = dag_data
            except (OSError, json.JSONDecodeError, ValueError) as dag_err:
                logger.warning(
                    "Failed to load GlobalDAG from %s: %s",
                    global_dag_path,
                    dag_err,
                )

        # Write to file
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2)

        logger.info(
            "Exported %d graphs to: %s",
            len(all_graphs),
            output_path,
        )
        return True

    except (KeyError, OSError, json.JSONDecodeError, ValueError) as err:
        logger.error("Failed to export JSON: %s", err, exc_info=True)
        return False
