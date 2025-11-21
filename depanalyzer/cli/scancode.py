"""CLI command to run ScanCode and build a flat node->license map.

This command scans the source root of each graph (and optionally its
third-party dependency graphs) using the external `scancode` CLI and
produces a JSON mapping:

    {
        "<graph-node-id>": "<SPDX license expression>"
    }

Node identifiers align with the merged graph produced by
`depanalyzer scan -t -o`, using human-meaningful namespaces instead of
raw graph IDs for dependency graphs. Per-graph license maps are cached
under the graphs directory as `<graph_id>.licenses.json` for
incremental reuse.
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
from typing import Any, Dict, List, Set, Tuple

from depanalyzer.graph.id_utils import (
    apply_namespace_prefix,
    derive_dependency_namespace,
    ensure_namespace_unique,
)
from depanalyzer.graph.global_dag import GlobalDAG
from depanalyzer.graph.registry import GraphRegistry
from depanalyzer.graph.schema_utils import canonicalize_normalized_id
from depanalyzer.utils.path_utils import normalize_node_id

logger = logging.getLogger("depanalyzer.cli.scancode")


def _find_scancode_executable() -> str:
    """Locate the ScanCode CLI executable.

    The search order prefers explicit environment configuration while still
    handling common local checkouts.

    Returns:
        Absolute path to a runnable ScanCode executable.

    Raises:
        RuntimeError: When no usable executable is found.
    """
    candidates: list[str] = []
    for env_var in ("SCANCODE_BIN", "SCANCODE_CLI"):
        env_value = os.environ.get(env_var)
        if env_value:
            candidates.append(env_value)

    detected = shutil.which("scancode")
    if detected:
        candidates.append(detected)

    toolkit_root = os.environ.get("SCANCODE_TOOLKIT")
    if toolkit_root:
        candidates.append(str(Path(toolkit_root) / "scancode"))

    candidates.append(str(Path.cwd() / "scancode"))
    candidates.append(str(Path.cwd() / "scancode-toolkit" / "scancode"))
    candidates.append("/mnt/c/Workspace/oslic/scancode-toolkit/scancode")

    for candidate in candidates:
        try:
            resolved = Path(candidate).expanduser()
        except (TypeError, ValueError):
            continue

        try:
            if resolved.is_file() and os.access(resolved, os.X_OK):
                return str(resolved)
        except OSError:
            continue

    raise RuntimeError(
        "scancode CLI not found. Set SCANCODE_BIN/SCANCODE_CLI to the "
        "executable path or add it to PATH."
    )


def _resolve_existing_path(raw_path: str | None) -> Path | None:
    """Best-effort conversion of stored paths into an existing Path."""
    if not raw_path:
        return None

    candidates: List[Path] = []
    raw = str(raw_path)
    candidates.append(Path(raw))

    # Handle Windows-style separators
    if "\\" in raw:
        candidates.append(Path(raw.replace("\\", "/")))
        # Map Windows drive to WSL-style /mnt/<drive>/
        if len(raw) >= 2 and raw[1] == ":":
            drive = raw[0].lower()
            rest = raw[2:].lstrip("\\/").replace("\\", "/")
            candidates.append(Path(f"/mnt/{drive}/{rest}"))

    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate.resolve()
        except OSError:
            continue
    return None


def _resolve_graphs_root(
    cache_dir_arg: str | None,
    source: str | None,
) -> Path:
    """Resolve graphs root directory for ScanCode.

    This mirrors the scan command layout where graphs live under
    <cache-dir>/<source_stem>/graphs by default, while still
    accepting callers that point directly at a graphs directory.
    """
    base_cache = (
        Path(cache_dir_arg).expanduser().resolve()
        if cache_dir_arg
        else Path(".dep_cache").expanduser().resolve()
    )

    if (base_cache / "registry.json").is_file():
        return base_cache

    if source:
        candidate = base_cache / Path(source).stem / "graphs"
        if candidate.is_dir():
            return candidate

    direct_graphs = base_cache / "graphs"
    if (direct_graphs / "registry.json").is_file():
        return direct_graphs

    candidates: List[Path] = []
    if base_cache.is_dir():
        for child in base_cache.iterdir():
            graphs_dir = child / "graphs"
            if (graphs_dir / "registry.json").is_file():
                candidates.append(graphs_dir)

    if len(candidates) == 1:
        return candidates[0]

    if not candidates:
        raise RuntimeError(
            f"No graphs registry found under {base_cache}. "
            "Provide --cache-dir pointing to your scan cache or use --source "
            "to derive the expected subdirectory."
        )

    raise RuntimeError(
        "Multiple graph caches found; please specify the intended project via "
        "--cache-dir or --source. Candidates: " + ", ".join(str(c) for c in candidates)
    )


def _discover_graph_sets(
    graphs_root: Path,
    include_deps: bool,
) -> Tuple[List[str], Set[str]]:
    """Discover graph IDs to scan.

    Args:
        graphs_root: Root directory for graph cache files.
        include_deps: Whether to include third-party dependency graphs.

    Returns:
        Tuple of (graph IDs to scan, root graph IDs).
    """
    registry = GraphRegistry.get_instance(cache_root=graphs_root)
    all_graphs = registry.list_graphs()

    dag = GlobalDAG.get_instance(cache_dir=graphs_root)
    root_ids: List[str] = []

    for gid in all_graphs:
        if not dag.get_dependents(gid):
            root_ids.append(gid)

    if not root_ids:
        root_ids = list(all_graphs)

    if not include_deps:
        return root_ids or all_graphs, set(root_ids)

    # Include root graphs and all of their transitive dependencies.
    selected: Set[str] = set()

    for gid in root_ids:
        selected.add(gid)
        selected.update(dag.get_transitive_dependencies(gid))

    targets = sorted(selected) if selected else all_graphs
    return targets, set(root_ids)


def _canonicalize_license_map(license_map: Dict[str, str]) -> Dict[str, str]:
    """Canonicalize node IDs to match exported graph IDs."""
    result: Dict[str, str] = {}
    for node_id, expr in license_map.items():
        expr_str = _normalize_spdx_expression(expr)
        if not expr_str:
            continue
        result[canonicalize_normalized_id(str(node_id))] = expr_str
    return result


def _apply_namespace_to_license_map(
    graph_id: str,
    license_map: Dict[str, str],
    graph_namespace: str | None,
    is_dependency: bool,
) -> Dict[str, str]:
    """Build node->license map aligned with merged graph IDs.

    Args:
        graph_id: Current graph identifier.
        license_map: Canonicalized node->license mapping for this graph.
        graph_namespace: Derived namespace for this graph.
        is_dependency: Whether this graph should be prefixed as a dependency.

    Returns:
        Mapping from merged-graph node IDs to SPDX expressions.
    """
    result: Dict[str, str] = {}
    for node_id, expr in license_map.items():
        expr_str = str(expr).strip()
        if not expr_str:
            continue
        merged_id = apply_namespace_prefix(node_id, graph_namespace, is_dependency)
        result[merged_id] = expr_str
    return result


def _normalize_spdx_expression(expr: Any) -> str | None:
    """Normalize a raw ScanCode expression value to a clean SPDX string.

    Args:
        expr: Raw expression value from ScanCode output.

    Returns:
        Trimmed SPDX expression or None when empty.
    """
    if expr is None:
        return None
    expr_str = str(expr).strip()
    return expr_str or None


def _extract_spdx_expression(fentry: Dict[str, Any]) -> str | None:
    """Extract the best SPDX expression from a ScanCode file entry.

    Args:
        fentry: File entry dictionary from ScanCode output.

    Returns:
        SPDX expression string, or None when missing.
    """
    for key in (
        "license_expression_spdx",
        "detected_license_expression_spdx",
        "declared_license_expression_spdx",
    ):
        expr = _normalize_spdx_expression(fentry.get(key))
        if expr:
            return expr

    license_entries = fentry.get("licenses") or []
    for lic in license_entries:
        expr = _normalize_spdx_expression(
            lic.get("spdx_license_key")
            or lic.get("license_expression_spdx")
            or lic.get("license_expression")
            or lic.get("key")
        )
        if expr:
            return expr

    return None


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
    scancode_bin = _find_scancode_executable()

    if not root_path.is_dir():
        raise RuntimeError(f"ScanCode root path is not a directory: {root_path}")

    with tempfile.NamedTemporaryFile(
        suffix=".json", delete=False, prefix="scancode_"
    ) as tmp:
        tmp_path = Path(tmp.name)

    try:
        cmd = [
            scancode_bin,
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
            logger.warning(
                "ScanCode exited with code %s for %s, attempting to parse partial results. stderr: %s",
                proc.returncode,
                root_path,
                proc.stderr.strip(),
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
            expr_str = _extract_spdx_expression(fentry)
            if not expr_str:
                # Skip files without a detected license instead of writing NOASSERTION.
                continue

            abs_path = (root_path / rel_path).resolve()
            node_id = normalize_node_id(abs_path, root_path, namespace=namespace)
            license_map[node_id] = expr_str

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
        source_arg = getattr(args, "source", None)
        output = Path(args.output)
        include_deps = getattr(args, "third_party", False)
        force = getattr(args, "force", False)
        source_root = _resolve_existing_path(source_arg)

        graphs_root = _resolve_graphs_root(cache_dir_arg, source_arg)

        logger.info("Using graphs root: %s", graphs_root)

        # Discover which graphs to scan
        target_graphs, root_graphs = _discover_graph_sets(
            graphs_root, include_deps
        )
        if not target_graphs:
            logger.error("No graphs found in registry under %s", graphs_root)
            return 1

        logger.info("Scanning licenses for %d graph(s)", len(target_graphs))

        registry = GraphRegistry.get_instance(cache_root=graphs_root)

        node_license_map: Dict[str, str] = {}
        namespace_assignments: Dict[str, str] = {}

        for graph_id in target_graphs:
            cache_entry = registry.get_entry(graph_id)
            if not cache_entry:
                logger.warning("Graph %s not found in registry, skipping", graph_id)
                continue

            summary = cache_entry.get("summary") or {}

            cache_path = Path(cache_entry["cache_path"])
            if not cache_path.is_file():
                fallback_path = graphs_root / f"{graph_id}.json"
                if fallback_path.is_file():
                    logger.info(
                        "Using graphs_root fallback for %s (registry path missing: %s)",
                        graph_id,
                        cache_path,
                    )
                    cache_path = fallback_path
                else:
                    logger.warning(
                        "Cached graph file missing for %s: %s (also tried %s)",
                        graph_id,
                        cache_path,
                        fallback_path,
                    )
                    continue

            # Load graph metadata to get root_path and namespace
            with cache_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            metadata = data.get("metadata", {}) or {}
            graph_section = data.get("graph")
            if isinstance(graph_section, dict) and graph_section.get("nodes"):
                graph_body = graph_section
            else:
                graph_body = data
            graph_nodes = graph_body.get("nodes") if isinstance(graph_body, dict) else None
            graph_node_ids = {
                n.get("id") for n in (graph_nodes or []) if isinstance(n, dict) and n.get("id")
            }
            base_namespace = derive_dependency_namespace(graph_id, metadata, summary)
            graph_namespace = ensure_namespace_unique(
                base_namespace, graph_id, namespace_assignments
            )

            root_path_str = metadata.get("root_path")
            path_namespace = metadata.get("path_namespace")
            scancode_namespace = path_namespace

            if path_namespace and graph_node_ids is not None:
                ns_prefix = f"//{path_namespace}"
                graph_has_namespace = any(
                    node_id.startswith(ns_prefix) for node_id in graph_node_ids
                )
                if not graph_has_namespace:
                    scancode_namespace = None

            root_path = _resolve_existing_path(root_path_str)
            if root_path is None:
                source_fallback = _resolve_existing_path(
                    data.get("source") or summary.get("source")
                )
                if source_fallback:
                    root_path = source_fallback
                else:
                    logger.warning(
                        "Graph %s has no root_path metadata and no usable source, skipping",
                        graph_id,
                    )
                    continue

            source_hint = _resolve_existing_path(
                summary.get("source") or data.get("source")
            )
            is_main_graph = False
            if source_root is not None:
                try:
                    if root_path and root_path.resolve() == source_root.resolve():
                        is_main_graph = True
                    elif source_hint and source_hint.resolve() == source_root.resolve():
                        is_main_graph = True
                except OSError:
                    is_main_graph = False
            elif graph_id in root_graphs:
                is_main_graph = True

            if not include_deps and not is_main_graph:
                logger.debug(
                    "Skipping %s (non-main graph, third-party scanning disabled)",
                    graph_id,
                )
                continue

            # Check for cached license map
            license_cache_path = graphs_root / f"{graph_id}.licenses.json"
            if license_cache_path.is_file() and not force:
                try:
                    with license_cache_path.open("r", encoding="utf-8") as f:
                        license_map = json.load(f)
                    canonical_map = _canonicalize_license_map(license_map)
                    if graph_node_ids is not None:
                        filtered_map = {
                            k: v for k, v in canonical_map.items() if k in graph_node_ids
                        }
                    else:
                        filtered_map = canonical_map
                    if canonical_map != license_map:
                        try:
                            with license_cache_path.open("w", encoding="utf-8") as f:
                                json.dump(filtered_map, f, indent=2)
                        except (OSError, TypeError, ValueError):
                            logger.debug(
                                "Could not rewrite canonicalized cache for %s",
                                graph_id,
                            )
                    node_license_map.update(
                        _apply_namespace_to_license_map(
                            graph_id,
                            filtered_map,
                            graph_namespace,
                            include_deps and not is_main_graph,
                        )
                    )
                    logger.info(
                        "Loaded cached license map for %s (%d entries)",
                        graph_id,
                        len(filtered_map),
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
                license_map = _run_scancode(root_path, scancode_namespace)
            except (
                RuntimeError,
                OSError,
                json.JSONDecodeError,
                ValueError,
            ) as e:
                logger.error("ScanCode failed for graph %s: %s", graph_id, e)
                return 1

            canonical_map = _canonicalize_license_map(license_map)
            if graph_node_ids is not None:
                filtered_map = {
                    k: v for k, v in canonical_map.items() if k in graph_node_ids
                }
            else:
                filtered_map = canonical_map

            # Cache per-graph license map
            try:
                with license_cache_path.open("w", encoding="utf-8") as f:
                    json.dump(filtered_map, f, indent=2)
                logger.info(
                    "Cached license map for %s at %s", graph_id, license_cache_path
                )
            except (OSError, TypeError, ValueError) as e:
                logger.warning("Failed to cache license map for %s: %s", graph_id, e)

            node_license_map.update(
                _apply_namespace_to_license_map(
                    graph_id,
                    filtered_map,
                    graph_namespace,
                    include_deps and not is_main_graph,
                )
            )
            logger.info(
                "Captured %d license entries for %s (namespace=%s)",
                len(filtered_map),
                graph_id,
                graph_namespace,
            )

        # Write combined result
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as f:
            json.dump(node_license_map, f, indent=2)

        logger.info(
            "License map exported to %s for %d graph(s) (%d node entries)",
            output,
            len(target_graphs),
            len(node_license_map),
        )
        return 0

    except RuntimeError as e:
        logger.error(str(e))
        return 1
    except (OSError, json.JSONDecodeError, ValueError) as e:
        logger.error("scancode command failed: %s", e, exc_info=True)
        return 1
