"""CLI command to run ScanCode and build a flat node->license map.

This command can scan a user-provided directory directly, or reuse cached
graphs from `depanalyzer scan` (and optionally their third-party dependency
graphs) using the external `scancode` CLI. The output is a JSON mapping:

    {
        "<graph-node-id>": "<SPDX license expression>"
    }

When graph metadata is available, node identifiers align with the merged graph
produced by `depanalyzer scan -t -o`, using human-meaningful namespaces instead
of raw graph IDs for dependency graphs. Per-graph license maps are cached under
the graphs directory as `<graph_id>.licenses.json` for incremental reuse.
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

from depanalyzer.graph import GlobalDAG, GraphRegistry, canonicalize_normalized_id
from depanalyzer.graph.id_utils import (
    apply_namespace_prefix,
    derive_dependency_namespace,
    ensure_namespace_unique,
)
from depanalyzer.graph.models.schema import NodeType
from depanalyzer.utils.path_utils import normalize_node_id
from depanalyzer.utils.scancode_installer import (
    DEFAULT_INSTALL_ROOT,
    get_installed_scancode_path,
    is_scancode_executable,
)

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

    installed = get_installed_scancode_path(DEFAULT_INSTALL_ROOT)
    if is_scancode_executable(installed):
        candidates.append(str(installed))

    candidates.append(str(Path.cwd() / "scancode"))
    candidates.append(str(Path.cwd() / "scancode.bat"))
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
        "ScanCode CLI not found. Set SCANCODE_BIN/SCANCODE_CLI to the executable "
        'path, install it in PATH, or run "depanalyzer --install" to download a '
        f"local copy under {DEFAULT_INSTALL_ROOT}."
    )


def _prepare_scancode_runtime(scancode_bin: str) -> None:
    """Ensure ScanCode runtime is ready before invocation.

    On Windows, ScanCode relies on a virtualenv created by configure.bat; if a
    prior attempt failed before installing the wheel, scancode.bat would skip
    reconfiguration. To recover, drop the virtualenv when the scancode script is
    missing so the next call reconfigures cleanly.
    """
    try:
        bin_path = Path(scancode_bin)
    except Exception:
        return

    if bin_path.suffix.lower() != ".bat":
        return

    root_dir = bin_path.parent
    venv_dir = root_dir / "venv"
    scancode_candidates = [
        venv_dir / "Scripts" / "scancode",
        venv_dir / "Scripts" / "scancode.exe",
    ]
    if any(c.exists() for c in scancode_candidates):
        return

    logger.info(
        "Resetting ScanCode virtualenv at %s to repair incomplete installation",
        venv_dir,
    )
    try:
        shutil.rmtree(venv_dir)
    except OSError:
        logger.debug("Failed to remove %s during repair", venv_dir)


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

    if (base_cache / "registry.db").is_file():
        return base_cache

    if source:
        candidate = base_cache / Path(source).stem / "graphs"
        if candidate.is_dir():
            return candidate

    direct_graphs = base_cache / "graphs"
    if (direct_graphs / "registry.db").is_file():
        return direct_graphs

    candidates: List[Path] = []
    if base_cache.is_dir():
        for child in base_cache.iterdir():
            graphs_dir = child / "graphs"
            if (graphs_dir / "registry.db").is_file():
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


def _load_dependency_metadata(root_path: Path) -> Dict[str, Any] | None:
    """Load dependency metadata written by fetchers.

    Args:
        root_path: Dependency root path.

    Returns:
        Parsed metadata dictionary when available, otherwise None.
    """
    candidates = [root_path / ".hvigor_meta.json"]
    for meta_path in candidates:
        try:
            if not meta_path.is_file():
                continue
            with meta_path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.debug("Failed reading dependency metadata from %s: %s", meta_path, exc)
    return None


def _extract_embedded_dependency_metadata(
    graph_metadata: Dict[str, Any], graph_nodes: List[Dict[str, Any]]
) -> Dict[str, Any] | None:
    """Extract dependency metadata that was embedded in exported graphs.

    Args:
        graph_metadata: Graph-level metadata dictionary.
        graph_nodes: List of graph nodes (raw export format).

    Returns:
        Embedded dependency metadata if present, otherwise None.
    """
    if isinstance(graph_metadata, dict):
        embedded = graph_metadata.get("dependency_metadata")
        if isinstance(embedded, dict) and embedded:
            return embedded
        meta_license = graph_metadata.get("license")
        meta_license_only = graph_metadata.get("license_only")
        if meta_license is not None and meta_license_only is not None:
            candidate: Dict[str, Any] = {
                "license": meta_license,
                "license_only": bool(meta_license_only),
            }
            resolved_version = graph_metadata.get("resolved_version")
            if resolved_version:
                candidate["resolved_version"] = resolved_version
            if graph_metadata.get("name"):
                candidate["name"] = graph_metadata.get("name")
            return candidate

    for node in graph_nodes:
        if not isinstance(node, dict):
            continue
        node_meta = node.get("dependency_metadata")
        if isinstance(node_meta, dict) and node_meta:
            return node_meta

    for node in graph_nodes:
        if not isinstance(node, dict):
            continue
        lic_expr = node.get("license")
        lic_only = node.get("license_only")
        if lic_expr is None or lic_only is None:
            continue

        candidate: Dict[str, Any] = {
            "license": lic_expr,
            "license_only": bool(lic_only),
        }
        resolved_version = node.get("resolved_version") or node.get("version")
        if resolved_version:
            candidate["resolved_version"] = resolved_version
        if node.get("name"):
            candidate["name"] = node.get("name")
        return candidate

    return None


def _resolve_dependency_metadata(
    root_path: Path | None,
    graph_metadata: Dict[str, Any],
    graph_nodes: List[Dict[str, Any]],
) -> Dict[str, Any] | None:
    """Resolve dependency metadata from filesystem or embedded export.

    Args:
        root_path: Dependency root path (if available).
        graph_metadata: Graph metadata loaded from export.
        graph_nodes: Graph node list loaded from export.

    Returns:
        Best-effort metadata dictionary, or None when missing.
    """
    if root_path:
        dep_metadata = _load_dependency_metadata(root_path)
        if dep_metadata:
            return dep_metadata

    return _extract_embedded_dependency_metadata(graph_metadata or {}, graph_nodes)


def _rank_node_id(node_id: str) -> Tuple[int, int]:
    """Rank node IDs so shallower IDs are preferred when attaching metadata."""
    parts = [segment for segment in str(node_id).strip("/").split("/") if segment]
    return (len(parts), len(str(node_id)))


def _pick_license_node_from_graph(
    graph_nodes: List[Dict[str, Any]], dep_metadata: Dict[str, Any] | None
) -> str | None:
    """Select the best graph node to attach metadata-derived licenses."""
    preferred_types = {
        NodeType.MODULE.value,
        NodeType.EXTERNAL_DEP.value,
        NodeType.EXTERNAL_LIBRARY.value,
        NodeType.HAP.value,
        NodeType.HAR.value,
        NodeType.HSP.value,
    }

    target_name = str(dep_metadata.get("name") or "").lower() if dep_metadata else ""
    target_version = str(
        dep_metadata.get("resolved_version")
        or dep_metadata.get("version")
        or ""
    ).lower() if dep_metadata else ""

    best_id: str | None = None
    best_score = -1
    best_rank = (10_000, 10_000)

    for node in graph_nodes:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        if not node_id:
            continue
        node_type = str(node.get("type") or "").lower()
        node_name = str(node.get("name") or "").lower()
        node_version = str(node.get("version") or "").lower()
        label_lower = str(node.get("label") or "").lower()
        id_lower = str(node_id).lower()

        match_name = bool(target_name) and (
            node_name == target_name
            or target_name in label_lower
            or target_name in id_lower
        )
        match_version = bool(target_version) and (
            node_version == target_version
            or target_version in label_lower
            or target_version in id_lower
        )

        if not match_name and not match_version:
            continue

        score = 0
        if match_name:
            score += 3
        if match_version:
            score += 2
        if node_type in preferred_types:
            score += 1

        rank = _rank_node_id(str(node_id))
        if score > best_score or (score == best_score and rank < best_rank):
            best_id = str(node_id)
            best_score = score
            best_rank = rank

    return best_id


def _merge_metadata_license(
    dep_metadata: Dict[str, Any] | None,
    graph_nodes: List[Dict[str, Any]],
    graph_node_ids: Set[str],
    license_map: Dict[str, str],
) -> bool:
    """Inject license map entries from dependency metadata when available."""
    if not dep_metadata:
        return False

    if not dep_metadata.get("license_only"):
        return False

    license_expr = _normalize_spdx_expression(dep_metadata.get("license"))
    if not license_expr:
        return False

    target_id = _pick_license_node_from_graph(graph_nodes, dep_metadata)
    if not target_id:
        return False

    canonical_id = canonicalize_normalized_id(str(target_id))
    if graph_node_ids and canonical_id not in graph_node_ids:
        return False

    if canonical_id in license_map:
        return False

    license_map[canonical_id] = license_expr
    return True


def _derive_graph_namespace(
    graph_id: str,
    metadata: Dict[str, Any],
    summary: Dict[str, Any],
    namespace_assignments: Dict[str, str],
) -> str:
    """Compute or reuse namespace for a graph."""
    base_namespace = derive_dependency_namespace(graph_id, metadata, summary)
    return ensure_namespace_unique(base_namespace, graph_id, namespace_assignments)


def _read_graph_payload(
    graph_id: str, graphs_root: Path, registry: GraphRegistry
) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    """Load graph JSON payload (metadata + nodes) for a graph ID."""
    cache_entry = registry.get_entry(graph_id)
    summary = cache_entry.get("summary") if cache_entry else {}

    cache_path: Path | None = None
    if cache_entry:
        cache_path = Path(cache_entry.get("cache_path", ""))
    if cache_path is None or not cache_path.is_file():
        fallback_path = graphs_root / f"{graph_id}.json"
        cache_path = fallback_path if fallback_path.is_file() else None

    if cache_path is None:
        raise FileNotFoundError(f"Graph {graph_id} cache not found")

    with cache_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    metadata = data.get("metadata", {}) or {}
    graph_section = data.get("graph")
    if isinstance(graph_section, dict) and graph_section.get("nodes"):
        graph_body = graph_section
    else:
        graph_body = data
    graph_nodes = graph_body.get("nodes") if isinstance(graph_body, dict) else None

    return metadata, summary or {}, graph_nodes or []


def _find_parent_proxy_nodes(
    graph_id: str,
    graphs_root: Path,
    registry: GraphRegistry,
    namespace_assignments: Dict[str, str],
    graph_namespace_map: Dict[str, str],
    root_graphs: Set[str],
    include_deps: bool,
) -> List[str]:
    """Find parent proxy node IDs (merged namespace) that point to a child graph."""
    try:
        dag = GlobalDAG.get_instance(cache_dir=graphs_root)
        parent_ids = dag.get_dependents(graph_id)
    except Exception:
        return []

    merged_ids: List[str] = []

    for parent_id in parent_ids:
        try:
            metadata, summary, parent_nodes = _read_graph_payload(
                parent_id, graphs_root, registry
            )
        except Exception:
            continue

        parent_namespace = graph_namespace_map.get(parent_id)
        if parent_namespace is None:
            parent_namespace = _derive_graph_namespace(
                parent_id, metadata, summary, namespace_assignments
            )
            graph_namespace_map[parent_id] = parent_namespace

        is_dependency = include_deps and parent_id not in root_graphs

        for node in parent_nodes:
            if not isinstance(node, dict):
                continue
            if node.get("child_graph_id") != graph_id:
                continue
            node_id = node.get("id")
            if not node_id:
                continue
            canonical_id = canonicalize_normalized_id(str(node_id))
            merged_id = apply_namespace_prefix(
                canonical_id, parent_namespace, is_dependency
            )
            merged_ids.append(merged_id)

    return merged_ids


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
    _prepare_scancode_runtime(scancode_bin)

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

        work_dir = Path(scancode_bin).resolve().parent

        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            cwd=work_dir,
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


def _write_license_map(output_path: Path, license_map: Dict[str, str]) -> None:
    """Persist license map to disk in JSON format.

    Args:
        output_path: Destination path for the JSON file.
        license_map: Mapping from node ID to SPDX license expression.

    Returns:
        None
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(license_map, f, indent=2)


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
        direct_path_arg = getattr(args, "path", None)
        output = Path(args.output)
        include_deps = getattr(args, "third_party", False)
        force = getattr(args, "force", False)

        if direct_path_arg:
            direct_root = _resolve_existing_path(direct_path_arg)
            if direct_root is None or not direct_root.is_dir():
                logger.error(
                    "Direct ScanCode path is not a directory or cannot be resolved: %s",
                    direct_path_arg,
                )
                return 1

            if include_deps:
                logger.info(
                    "--third-party ignored for direct ScanCode path; dependency graphs require cached scan results."
                )
            if cache_dir_arg or source_arg:
                logger.info(
                    "Using direct path mode; cache-dir and source arguments are ignored."
                )

            try:
                license_map = _run_scancode(direct_root, None)
            except (
                RuntimeError,
                OSError,
                json.JSONDecodeError,
                ValueError,
            ) as e:
                logger.error("ScanCode failed for path %s: %s", direct_root, e)
                return 1

            canonical_map = _canonicalize_license_map(license_map)
            _write_license_map(output, canonical_map)
            logger.info(
                "License map exported to %s for %s (%d node entries)",
                output,
                direct_root,
                len(canonical_map),
            )
            return 0

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
        graph_namespace_map: Dict[str, str] = {}

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
                canonicalize_normalized_id(str(n.get("id")))
                for n in (graph_nodes or [])
                if isinstance(n, dict) and n.get("id")
            }
            graph_namespace = _derive_graph_namespace(
                graph_id, metadata, summary, namespace_assignments
            )
            graph_namespace_map[graph_id] = graph_namespace

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

            dep_metadata = _resolve_dependency_metadata(
                root_path, metadata, graph_nodes or []
            )
            dep_license_expr = _normalize_spdx_expression(
                dep_metadata.get("license") if dep_metadata else None
            )
            parent_proxy_ids: List[str] = []
            if dep_license_expr:
                parent_proxy_ids = _find_parent_proxy_nodes(
                    graph_id,
                    graphs_root,
                    registry,
                    namespace_assignments,
                    graph_namespace_map,
                    root_graphs,
                    include_deps,
                )

            # Check for cached license map
            license_cache_path = graphs_root / f"{graph_id}.licenses.json"
            if license_cache_path.is_file() and not force:
                try:
                    with license_cache_path.open("r", encoding="utf-8") as f:
                        license_map = json.load(f)
                    canonical_map = _canonicalize_license_map(license_map)
                    filtered_map = {
                        k: v
                        for k, v in canonical_map.items()
                        if not graph_node_ids or k in graph_node_ids
                    }
                    metadata_added = _merge_metadata_license(
                        dep_metadata,
                        graph_nodes or [],
                        graph_node_ids,
                        filtered_map,
                    )
                    cache_needs_update = metadata_added or canonical_map != license_map
                    if cache_needs_update:
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
                    # Add proxy node licenses directly (already in final format)
                    if dep_license_expr:
                        for proxy_id in parent_proxy_ids:
                            node_license_map.setdefault(proxy_id, dep_license_expr)
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
            filtered_map = {
                k: v for k, v in canonical_map.items() if not graph_node_ids or k in graph_node_ids
            }
            _merge_metadata_license(
                dep_metadata,
                graph_nodes or [],
                graph_node_ids,
                filtered_map,
            )
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
            # Add proxy node licenses directly (already in final format)
            if dep_license_expr:
                for proxy_id in parent_proxy_ids:
                    node_license_map.setdefault(proxy_id, dep_license_expr)
            logger.info(
                "Captured %d license entries for %s (namespace=%s)",
                len(filtered_map),
                graph_id,
                graph_namespace,
            )

        # Write combined result
        _write_license_map(output, node_license_map)

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
