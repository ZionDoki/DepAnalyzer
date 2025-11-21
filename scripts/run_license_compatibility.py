#!/usr/bin/env python3
"""Run depanalyzer scan, ScanCode, and liscopelens compatibility analysis end-to-end."""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

# Ensure repository root is importable when running as a script.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from depanalyzer.graph.id_utils import (
    apply_namespace_prefix,
    derive_dependency_namespace,
    ensure_namespace_unique,
)

# Default locations expected in this workspace.
DEFAULT_PROJECT = Path("/mnt/c/Workspace/dialog_hub-master")
DEFAULT_LISCOPLENS_PATH = Path("/mnt/c/Workspace/liscopelens")

logger = logging.getLogger("depanalyzer.scripts.license_pipeline")


def ensure_liscopelens_importable(liscopelens_path: Path) -> None:
    """Add liscopelens source tree to sys.path when it is not installed."""
    resolved = liscopelens_path.expanduser().resolve()
    if str(resolved) not in sys.path:
        sys.path.insert(0, str(resolved))
        logger.info("Added liscopelens to sys.path: %s", resolved)


def run_command(command: list[str], workdir: Path | None = None) -> None:
    """Run a subprocess command and raise on failure."""
    logger.info("Running command: %s", " ".join(command))
    result = subprocess.run(
        command,
        cwd=str(workdir) if workdir else None,
        check=False,
        text=True,
        capture_output=True,
    )
    if result.stdout:
        logger.debug("stdout:\n%s", result.stdout)
    if result.stderr:
        logger.debug("stderr:\n%s", result.stderr)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code {result.returncode}: {' '.join(command)}"
        )


def normalize_graph_dict(raw_graph: dict[str, Any]) -> dict[str, Any]:
    """Convert depanalyzer node-link graph to liscopelens-friendly shape."""
    graph_data = dict(raw_graph)
    if "edges" not in graph_data and "links" in graph_data:
        graph_data["edges"] = graph_data.pop("links")
    return graph_data


def _to_posix_path(path_str: str) -> str:
    """Convert Windows-style cache paths to WSL/posix style if needed."""
    if ":" in path_str and "\\" in path_str:
        drive, rest = path_str.split(":", 1)
        rest = rest.lstrip("\\/").replace("\\", "/")
        return f"/mnt/{drive.lower()}/{rest}"
    return path_str


def _load_registry(cache_dir: Path) -> dict[str, Any]:
    """Load graph registry from cache dir."""
    reg_path = cache_dir / "registry.json"
    with reg_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_cached_graph(cache_path: Path) -> Tuple[Any, dict[str, Any]]:
    """Load a cached graph file into a NetworkX MultiDiGraph."""
    from networkx.readwrite import node_link_graph  # lazy import

    with cache_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    # Support both {directed,multigraph,nodes,links} and {"graph": {...}}
    graph_body = None
    if isinstance(data, dict):
        if {"nodes", "links"}.issubset(data.keys()):
            graph_body = data
        elif isinstance(data.get("graph"), dict) and "nodes" in data["graph"]:
            graph_body = data["graph"]

    if graph_body is None:
        raise ValueError(f"Unsupported graph format in {cache_path}")

    g = node_link_graph(graph_body, edges="links")
    metadata = data.get("metadata", {})
    return g, metadata


def _build_combined_graph(cache_dir: Path, project_path: Path, include_deps: bool = True) -> Tuple[dict[str, Any], dict[str, Any], set[str]]:
    """Merge cached graphs into one, optionally including dependencies."""
    from networkx import MultiDiGraph
    from networkx.readwrite import node_link_data

    registry = _load_registry(cache_dir)
    root_path = project_path.expanduser().resolve()

    def is_main(entry: dict[str, Any]) -> bool:
        src = _to_posix_path(entry.get("summary", {}).get("source", ""))
        try:
            return Path(src).resolve() == root_path
        except Exception:
            return False

    main_ids = {gid for gid, entry in registry.items() if is_main(entry)}
    combined = MultiDiGraph()
    merged_from: list[str] = []
    graph_namespaces: Dict[str, str] = {}
    namespace_assignments: Dict[str, str] = {}

    for graph_id, entry in registry.items():
        cache_path = Path(_to_posix_path(entry["cache_path"])).expanduser()
        if not cache_path.exists():
            alt = cache_dir / f"{graph_id}.json"
            cache_path = alt if alt.exists() else cache_path
        if not cache_path.exists():
            logger.warning("Skip graph %s (cache file missing at %s)", graph_id, cache_path)
            continue
        summary = entry.get("summary") or {}
        try:
            g, meta = _load_cached_graph(cache_path)
        except Exception as exc:
            logger.warning("Skip graph %s (load error: %s)", graph_id, exc)
            continue

        base_namespace = derive_dependency_namespace(graph_id, meta, summary)
        namespace = ensure_namespace_unique(
            base_namespace, graph_id, namespace_assignments
        )
        graph_namespaces[graph_id] = namespace
        is_dependency = graph_id not in main_ids
        if not include_deps and is_dependency:
            continue
        for node_id, attrs in g.nodes(data=True):
            new_id = apply_namespace_prefix(
                str(node_id),
                namespace,
                is_dependency,
            )
            new_attrs = dict(attrs or {})
            new_attrs.setdefault("orig_id", str(node_id))
            new_attrs.setdefault("graph_id", graph_id)
            new_attrs.setdefault("graph_namespace", namespace)
            combined.add_node(new_id, **new_attrs)

        for u, v, attrs in g.edges(data=True):
            new_u = apply_namespace_prefix(str(u), namespace, is_dependency)
            new_v = apply_namespace_prefix(str(v), namespace, is_dependency)
            combined.add_edge(new_u, new_v, **(attrs or {}))

        merged_from.append(graph_id)

    graph_data = node_link_data(combined, edges="links")
    metadata = {
        "graph_id": next(iter(main_ids)) if main_ids else "merged_graph",
        "merged": True,
        "merged_from": merged_from,
        "node_count": combined.number_of_nodes(),
        "edge_count": combined.number_of_edges(),
        "graph_namespaces": graph_namespaces,
    }
    return graph_data, metadata, main_ids


def _normalize_spdx_expression(expr: str) -> str:
    """Coerce license expressions to SPDX-friendly case (best effort)."""
    tokens = re.findall(r"[A-Za-z0-9.\-+]+|\(|\)|AND|OR|WITH", expr, flags=re.IGNORECASE)
    normalized: list[str] = []
    for tok in tokens:
        upper = tok.upper()
        if tok in ("(", ")"):
            normalized.append(tok)
        elif upper in ("AND", "OR", "WITH"):
            normalized.append(upper)
        else:
            normalized.append(tok)
    return " ".join(normalized)


def _flatten_license_map(scancode_path: Path, main_ids: set[str]) -> dict[str, str]:
    """Load ScanCode output into a single node->license map."""
    with scancode_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    if isinstance(data, dict) and data and all(isinstance(v, str) for v in data.values()):
        return {
            str(node_id): _normalize_spdx_expression(str(expr))
            for node_id, expr in data.items()
        }

    if isinstance(data, dict) and isinstance(data.get("node_licenses"), dict):
        return {
            str(node_id): _normalize_spdx_expression(str(expr))
            for node_id, expr in data["node_licenses"].items()
        }

    flat: Dict[str, str] = {}
    if isinstance(data, dict):
        for graph_id, mapping in data.items():
            if not isinstance(mapping, dict):
                continue
            prefix = None if graph_id in main_ids else f"dep:{graph_id}:"
            for node_id, expr in mapping.items():
                raw_id = str(node_id)
                nid = raw_id if raw_id.startswith(("//", "dep:")) else f"//{raw_id.lstrip('/')}"
                merged_id = f"{prefix}{nid}" if prefix else nid
                flat[merged_id] = _normalize_spdx_expression(str(expr))
    return flat


def _build_node_set(graph_dict: dict[str, Any]) -> set[str]:
    """Extract node identifiers from a node-link graph dict.

    Args:
        graph_dict: Graph dictionary with a ``nodes`` collection.

    Returns:
        Set of node identifier strings.
    """
    nodes = graph_dict.get("nodes") or []
    return {str(node.get("id")) for node in nodes if isinstance(node, dict) and node.get("id")}


def _get_context_node_count(context: Any) -> int | None:
    """Best-effort extraction of node count from liscopelens GraphManager.

    Args:
        context: Compatibility context object returned by liscopelens.

    Returns:
        Node count if discoverable, otherwise None.
    """
    try_attrs = [
        getattr(context, "graph", None),
        getattr(context, "graph_data", None),
        getattr(context, "nx_graph", None),
    ]
    for graph_obj in try_attrs:
        if graph_obj is None:
            continue
        try:
            if hasattr(graph_obj, "number_of_nodes"):
                return int(graph_obj.number_of_nodes())  # type: ignore[call-arg]
            nodes = getattr(graph_obj, "nodes", None)
            if callable(nodes):
                return len(list(nodes()))
        except Exception:
            continue
    return None


def _validate_alignment(
    graph_dict: dict[str, Any],
    license_map: dict[str, str],
    context: Any,
    logger_obj: logging.Logger,
) -> dict[str, Any]:
    """Validate ScanCode/license alignment and node counts.

    Args:
        graph_dict: Merged graph data in node-link format.
        license_map: Flattened node->license mapping.
        context: Compatibility context returned by liscopelens.
        logger_obj: Logger for emitting validation results.

    Returns:
        Dict of validation metrics and counts.
    """
    node_ids = _build_node_set(graph_dict)
    license_keys = set(license_map.keys())
    matched = len(node_ids & license_keys)
    license_coverage = matched / max(len(license_keys), 1)
    missing_from_graph = len(license_keys) - matched

    context_nodes = _get_context_node_count(context)
    graph_node_count = len(node_ids)

    logger_obj.info(
        "License coverage: %d/%d (%.2f%%) license nodes found in graph; missing %d",
        matched,
        len(license_keys),
        license_coverage * 100.0,
        missing_from_graph,
    )

    if context_nodes is not None:
        logger_obj.info(
            "Context node count: %d vs merged graph nodes: %d",
            context_nodes,
            graph_node_count,
        )
        if context_nodes != graph_node_count:
            logger_obj.warning(
                "Context node count mismatch (context=%d, graph=%d)",
                context_nodes,
                graph_node_count,
            )

    return {
        "graph_nodes": graph_node_count,
        "license_entries": len(license_keys),
        "license_matched": matched,
        "license_coverage": license_coverage,
        "context_nodes": context_nodes,
        "context_matches_graph": (
            context_nodes == graph_node_count if context_nodes is not None else None
        ),
    }


def patch_liscopelens_decycle() -> None:
    """Monkey-patch liscopelens decycle to avoid exponential simple_cycles scans."""
    try:
        import networkx as nx  # type: ignore
        from liscopelens.parser import decycle  # type: ignore
    except Exception as exc:  # pragma: no cover - defensive import
        logger.warning("Skipped decycle patch (import failed): %s", exc)
        return

    def fast_detect(self, graph):  # type: ignore[override]
        # Disable condensation by reporting no cycles; keeps node counts intact.
        return []

    def allow_none(self, cycle, context):  # type: ignore[override]
        return False

    decycle.DecycleParser._detect_cycles = fast_detect  # type: ignore[attr-defined]
    decycle.DecycleParser._is_code_cycle = allow_none  # type: ignore[attr-defined]
    logger.info("Patched liscopelens DecycleParser to bypass cycle condensation.")


def load_graph_for_liscopelens(graph_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load merged graph JSON and return (graph_dict, metadata)."""
    with graph_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    metadata = data.get("metadata", {})
    graph_body = data.get("graph") or data
    graph_dict = normalize_graph_dict(graph_body)
    return graph_dict, metadata


def load_license_map(license_map_path: Path) -> dict[str, str]:
    """Load aggregated node->license mapping from ScanCode output."""
    with license_map_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict) and data and all(isinstance(v, str) for v in data.values()):
        return {
            str(k): _normalize_spdx_expression(str(v)) for k, v in data.items()
        }
    node_map = data.get("node_licenses") if isinstance(data, dict) else None
    if isinstance(node_map, dict):
        return {
            str(k): _normalize_spdx_expression(str(v)) for k, v in node_map.items()
        }
    raise ValueError(f"Unrecognized license map shape in {license_map_path}")


def _jsonify(obj: Any) -> Any:
    """Recursively convert sets and frozensets to sorted lists for JSON export."""
    if isinstance(obj, dict):
        return {k: _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, (set, frozenset)):
        return sorted(_jsonify(v) for v in obj)
    if isinstance(obj, list):
        return [_jsonify(v) for v in obj]
    return obj


def run_pipeline(args: argparse.Namespace) -> None:
    """Execute scan → scancode → liscopelens compatibility pipeline."""
    ensure_liscopelens_importable(args.liscopelens_path)
    patch_liscopelens_decycle()
    from liscopelens.api import check_compatibility  # pylint: disable=import-error
    from liscopelens.utils.graph import GraphManager  # pylint: disable=import-error

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    graph_output = output_dir / "merged_graph.json"
    combined_graph_output = output_dir / "combined_graph.json"
    license_output = output_dir / "license_map.json"
    compatibility_output = output_dir / "compatibility_results.json"
    compatibility_graph_output = output_dir / "compatibility_graph.json"

    scan_cmd = [
        sys.executable,
        "-m",
        "depanalyzer.main",
        "scan",
        str(args.project_path),
        "-o",
        str(graph_output),
        "--cache-dir",
        str(args.cache_dir),
        "--workers",
        str(args.workers),
        "--max-depth",
        str(args.max_depth),
    ]
    if args.timeout:
        scan_cmd.extend(["--timeout", str(args.timeout)])
    if args.third_party:
        scan_cmd.append("--third-party")
    if args.max_deps:
        scan_cmd.extend(["--max-deps", str(args.max_deps)])

    run_command(scan_cmd)

    scancode_cmd = [
        sys.executable,
        "-m",
        "depanalyzer.main",
        "scancode",
        "-o",
        str(license_output),
        "--cache-dir",
        str(args.cache_dir),
        "--source",
        str(args.project_path),
    ]
    if args.third_party:
        scancode_cmd.append("--third-party")
    if args.force_scancode:
        scancode_cmd.append("--force")

    run_command(scancode_cmd)

    # Rebuild a combined graph and flattened license map from cache for liscopelens.
    combined_graph_dict, combined_meta, main_ids = _build_combined_graph(
        args.cache_dir / Path(args.project_path).name / "graphs"
        if (args.cache_dir / Path(args.project_path).name / "graphs").exists()
        else args.cache_dir / "graphs",
        args.project_path,
        include_deps=args.third_party,
    )
    license_map = _flatten_license_map(license_output, main_ids)

    # Write combined graph for inspection.
    with combined_graph_output.open("w", encoding="utf-8") as handle:
        json.dump({"metadata": combined_meta, "graph": combined_graph_dict}, handle, indent=2)

    context, results = check_compatibility(
        license_map,
        normalize_graph_dict(combined_graph_dict),
        args={"ignore_unk": True},
    )
    validation = _validate_alignment(
        normalize_graph_dict(combined_graph_dict),
        license_map,
        context,
        logger,
    )

    serialized_results = _jsonify(results)
    payload = {
        "metadata": combined_meta,
        "results": serialized_results,
        "validation": validation,
    }
    with compatibility_output.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    logger.info("Compatibility results written to %s", compatibility_output)

    if isinstance(context, GraphManager):
        context.save(str(compatibility_graph_output))
        logger.info("Compatibility graph saved to %s", compatibility_graph_output)


def build_parser() -> argparse.ArgumentParser:
    """Build command-line argument parser."""
    parser = argparse.ArgumentParser(
        description="Run depanalyzer scan + scancode + liscopelens compatibility check."
    )
    parser.add_argument(
        "--project-path",
        type=Path,
        default=DEFAULT_PROJECT,
        help="Repository to analyze (default: %(default)s).",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(".dep_cache"),
        help="Cache directory shared by scan and scancode (default: %(default)s).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("license_pipeline_output"),
        help="Output directory for graph, license map, and compatibility results.",
    )
    parser.add_argument(
        "--liscopelens-path",
        type=Path,
        default=DEFAULT_LISCOPLENS_PATH,
        help="Path to liscopelens source tree if not installed (default: %(default)s).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Worker count for depanalyzer scan (default: %(default)s).",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=3,
        help="Third-party dependency depth (default: %(default)s).",
    )
    parser.add_argument(
        "--max-deps",
        type=int,
        help="Global third-party dependency limit.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="Seconds to wait for scan to finish (default: %(default)s).",
    )
    parser.add_argument(
        "--no-third-party",
        dest="third_party",
        action="store_false",
        help="Disable third-party graph resolution (default: enabled).",
    )
    parser.add_argument(
        "--force-scancode",
        action="store_true",
        help="Force re-run ScanCode even if cached license maps exist.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    parser.set_defaults(third_party=True)
    return parser


def main() -> None:
    """Entrypoint for the pipeline script."""
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(levelname)s] %(message)s",
    )

    try:
        run_pipeline(args)
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Pipeline failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
