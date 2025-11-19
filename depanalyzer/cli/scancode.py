"""CLI command to run ScanCode and build per-graph license maps.

This command scans the source root of each graph (and optionally its
third-party dependency graphs) using the external `scancode` CLI and
produces a JSON mapping:

    {
        "<graph_id>": {
            "//path/to/file1": "MIT",
            "//path/to/file2": "Apache-2.0 OR GPL-3.0-only",
            ...
        },
        ...
    }

Per-graph license maps are also cached under the graphs directory as
`<graph_id>.licenses.json` for incremental reuse.
"""

# CLI intentionally shields unexpected ScanCode failures to keep process exit codes consistent.


from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Set

from depanalyzer.graph.global_dag import GlobalDAG
from depanalyzer.graph.registry import GraphRegistry
from depanalyzer.utils.path_utils import normalize_node_id

logger = logging.getLogger("depanalyzer.cli.scancode")


def _discover_graph_sets(
    graphs_root: Path,
    include_deps: bool,
) -> List[str]:
    """Discover graph IDs to scan.

    Args:
        graphs_root: Root directory for graph cache files.
        include_deps: Whether to include third-party dependency graphs.

    Returns:
        List[str]: Graph IDs to scan.
    """
    registry = GraphRegistry.get_instance(cache_root=graphs_root)
    all_graphs = registry.list_graphs()

    if not include_deps:
        # Only root graphs: those without parents in GlobalDAG.
        dag = GlobalDAG.get_instance(cache_dir=graphs_root)
        roots: List[str] = []
        for gid in all_graphs:
            if not dag.get_dependents(gid):
                roots.append(gid)
        return roots or all_graphs

    # Include root graphs and all of their transitive dependencies.
    dag = GlobalDAG.get_instance(cache_dir=graphs_root)
    selected: Set[str] = set()

    for gid in all_graphs:
        if not dag.get_dependents(gid):
            selected.add(gid)
            selected.update(dag.get_transitive_dependencies(gid))

    return sorted(selected) if selected else all_graphs


def _run_scancode(
    root_path: Path,
    namespace: str | None,
) -> Dict[str, str]:
    """Run ScanCode on a single repository and build path->license map.

    Args:
        root_path: Repository root to scan.
        namespace: Optional path namespace for normalization (for third-party
            graphs, e.g. repo directory name).

    Returns:
        Dict[str, str]: Mapping from normalized node ID to license expression.
    """
    if shutil.which("scancode") is None:
        raise RuntimeError(
            "scancode CLI not found on PATH. Please install ScanCode Toolkit "
            "and ensure the `scancode` command is available."
        )

    if not root_path.is_dir():
        raise RuntimeError(f"ScanCode root path is not a directory: {root_path}")

    with tempfile.NamedTemporaryFile(
        suffix=".json", delete=False, prefix="scancode_"
    ) as tmp:
        tmp_path = Path(tmp.name)

    try:
        cmd = [
            "scancode",
            "--license",
            "--strip-root",
            "--json-pp",
            str(tmp_path),
            str(root_path),
        ]
        logger.info("Running ScanCode: %s", " ".join(cmd))

        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            logger.error("ScanCode failed for %s: %s", root_path, proc.stderr)
            raise RuntimeError(
                f"ScanCode exited with code {proc.returncode} for {root_path}"
            )

        with tmp_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        files = data.get("files") or []
        license_map: Dict[str, str] = {}

        for fentry in files:
            # Skip directories; ScanCode typically marks them with type 'directory'
            if fentry.get("type") == "directory":
                continue

            rel_path = fentry.get("path")
            if not rel_path:
                continue

            # Derive license expression for this file.
            expr = fentry.get("license_expression") or fentry.get(
                "detected_license_expression"
            )
            if not expr:
                # Try legacy format: licenses list with per-match expressions
                licenses = fentry.get("licenses") or []
                if licenses:
                    expr = licenses[0].get("license_expression") or licenses[0].get(
                        "expression"
                    )

            if not expr:
                continue

            abs_path = (root_path / rel_path).resolve()
            node_id = normalize_node_id(abs_path, root_path, namespace=namespace)
            license_map[node_id] = expr

        logger.info(
            "ScanCode extracted %d licensed files under %s", len(license_map), root_path
        )
        return license_map
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def scancode_command(args) -> int:
    """Execute scancode command.

    Args:
        args: Parsed command-line arguments.

    Returns:
        int: Exit code.
    """
    try:
        cache_dir_arg = getattr(args, "cache_dir", None)
        output = Path(args.output)
        include_deps = getattr(args, "third_party", False)
        force = getattr(args, "force", False)

        # Determine graphs root. cache_dir is expected to point to the
        # per-project cache directory used during scan, typically:
        #   .dep_cache/<source_stem>
        # Graphs are stored under <cache_dir>/graphs.
        if cache_dir_arg:
            cache_dir = Path(cache_dir_arg).expanduser().resolve()
        else:
            cache_dir = Path(".dep_cache").expanduser().resolve()

        graphs_root = cache_dir / "graphs"

        graphs_root.mkdir(parents=True, exist_ok=True)

        logger.info("Using graphs root: %s", graphs_root)

        # Discover which graphs to scan
        target_graphs = _discover_graph_sets(graphs_root, include_deps)
        if not target_graphs:
            logger.error("No graphs found in registry under %s", graphs_root)
            return 1

        logger.info("Scanning licenses for %d graph(s)", len(target_graphs))

        registry = GraphRegistry.get_instance(cache_root=graphs_root)

        result: Dict[str, Dict[str, str]] = {}

        for graph_id in target_graphs:
            cache_entry = registry.get_entry(graph_id)
            if not cache_entry:
                logger.warning("Graph %s not found in registry, skipping", graph_id)
                continue

            cache_path = Path(cache_entry["cache_path"])
            if not cache_path.is_file():
                logger.warning(
                    "Cached graph file missing for %s: %s", graph_id, cache_path
                )
                continue

            # Load graph metadata to get root_path and namespace
            with cache_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            metadata = data.get("metadata", {}) or {}

            root_path_str = metadata.get("root_path")
            path_namespace = metadata.get("path_namespace")

            if not root_path_str:
                logger.warning("Graph %s has no root_path metadata, skipping", graph_id)
                continue

            root_path = Path(root_path_str)

            # Check for cached license map
            license_cache_path = graphs_root / f"{graph_id}.licenses.json"
            if license_cache_path.is_file() and not force:
                try:
                    with license_cache_path.open("r", encoding="utf-8") as f:
                        license_map = json.load(f)
                    result[graph_id] = license_map
                    logger.info(
                        "Loaded cached license map for %s (%d entries)",
                        graph_id,
                        len(license_map),
                    )
                    continue
                except (OSError, json.JSONDecodeError, ValueError) as e:
                    logger.warning(
                        "Failed to load cached license map for %s: %s; re-scanning",
                        graph_id,
                        e,
                    )

            # Run ScanCode for this graph
            try:
                license_map = _run_scancode(root_path, path_namespace)
            except (
                RuntimeError,
                OSError,
                json.JSONDecodeError,
                ValueError,
            ) as e:
                logger.error("ScanCode failed for graph %s: %s", graph_id, e)
                return 1

            # Cache per-graph license map
            try:
                with license_cache_path.open("w", encoding="utf-8") as f:
                    json.dump(license_map, f, indent=2)
                logger.info(
                    "Cached license map for %s at %s", graph_id, license_cache_path
                )
            except (OSError, TypeError, ValueError) as e:
                logger.warning("Failed to cache license map for %s: %s", graph_id, e)

            result[graph_id] = license_map

        # Write combined result
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)

        logger.info("License map exported to %s for %d graph(s)", output, len(result))
        return 0

    except RuntimeError as e:
        logger.error(str(e))
        return 1
    except (OSError, json.JSONDecodeError, ValueError) as e:
        logger.error("scancode command failed: %s", e, exc_info=True)
        return 1
