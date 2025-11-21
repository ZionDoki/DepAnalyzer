#!/usr/bin/env python3
"""Run depanalyzer scan + ScanCode + liscopelens compatibility end-to-end."""

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Set, Tuple, List

# Ensure repository root is importable when running as a script.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Default locations expected in this workspace.
DEFAULT_PROJECT = Path("/mnt/c/Workspace/dialog_hub-master")
DEFAULT_LISCOPLENS_PATH = Path("/mnt/c/Workspace/liscopelens")

logger = logging.getLogger("depanalyzer.scripts.license_pipeline")


def ensure_liscopelens_importable(liscopelens_path: Path) -> None:
    """Add liscopelens source tree to sys.path when it is not installed.

    Args:
        liscopelens_path: Path to the liscopelens repository.
    """
    resolved = liscopelens_path.expanduser().resolve()
    if str(resolved) not in sys.path:
        sys.path.insert(0, str(resolved))
        logger.info("Added liscopelens to sys.path: %s", resolved)


def run_command(command: List[str], workdir: Optional[Path] = None) -> None:
    """Run a subprocess command and raise on failure.

    Args:
        command: Command and arguments to execute.
        workdir: Optional working directory for the command.

    Raises:
        RuntimeError: When the command exits with a non-zero code.
    """
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


def load_graph(graph_path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Load a graph export produced by depanalyzer.

    Args:
        graph_path: Path to the JSON graph file.

    Returns:
        Tuple containing the graph dictionary and metadata.
    """
    with graph_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    graph_candidate = None
    if isinstance(data, dict):
        graph_section = data.get("graph")
        if isinstance(graph_section, dict) and graph_section.get("nodes"):
            graph_candidate = graph_section
        else:
            graph_candidate = data

    if not isinstance(graph_candidate, dict):
        raise ValueError(f"Unsupported graph format in {graph_path}")

    graph_dict = dict(graph_candidate)
    metadata = data.get("metadata") or graph_dict.get("metadata") or {}
    return graph_dict, metadata


def load_license_map(license_map_path: Path) -> Dict[str, str]:
    """Load aggregated node->license mapping from ScanCode output."""
    with license_map_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, Mapping):
        raise ValueError(f"License map is not a mapping: {license_map_path}")
    return {str(k): str(v) for k, v in data.items()}


def _normalize_spdx_expression(expr: str) -> str:
    """Coerce license expressions to SPDX-friendly case (best effort)."""
    import re

    tokens = re.findall(r"[A-Za-z0-9.\-+]+|\(|\)|AND|OR|WITH", expr, flags=re.IGNORECASE)
    normalized: List[str] = []
    for tok in tokens:
        upper = tok.upper()
        if tok in ("(", ")"):
            normalized.append(tok)
        elif upper in ("AND", "OR", "WITH"):
            normalized.append(upper)
        else:
            normalized.append(tok)
    return " ".join(normalized)


def _build_node_set(graph_dict: Dict[str, Any]) -> Set[str]:
    """Extract node identifiers from a node-link graph dict.

    Args:
        graph_dict: Graph dictionary with a ``nodes`` collection.

    Returns:
        Set of node identifier strings.
    """
    nodes = graph_dict.get("nodes") or []
    return {str(node.get("id")) for node in nodes if isinstance(node, dict) and node.get("id")}


def _get_context_node_count(context: Any) -> Optional[int]:
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
    graph_dict: Dict[str, Any],
    license_map: Mapping[str, str],
    context: Any,
    logger_obj: logging.Logger,
) -> Dict[str, Any]:
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
    license_keys = {str(k) for k in license_map.keys()}
    matched = len(node_ids & license_keys)
    license_coverage = matched / max(len(license_keys), 1)
    graph_coverage = matched / max(len(node_ids), 1)
    missing_from_graph = len(license_keys) - matched
    missing_license_nodes = len(node_ids) - matched

    context_nodes = _get_context_node_count(context)
    graph_node_count = len(node_ids)

    logger_obj.info(
        "License coverage: %d/%d (%.2f%%) license nodes found in graph; missing %d",
        matched,
        len(license_keys),
        license_coverage * 100.0,
        missing_from_graph,
    )
    logger_obj.info(
        "Graph coverage: %d/%d (%.2f%%) graph nodes have licenses; missing %d",
        matched,
        graph_node_count,
        graph_coverage * 100.0,
        missing_license_nodes,
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
        "graph_license_coverage": graph_coverage,
        "missing_license_nodes": missing_license_nodes,
        "context_nodes": context_nodes,
        "context_matches_graph": (
            context_nodes == graph_node_count if context_nodes is not None else None
        ),
    }


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
    from liscopelens.api import check_compatibility  # pylint: disable=import-error
    from liscopelens.utils.graph import GraphManager  # pylint: disable=import-error

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    graph_output = output_dir / "graph.json"
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
        scan_cmd.append("-t")
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
        scancode_cmd.append("-t")
    if args.force_scancode:
        scancode_cmd.append("--force")

    run_command(scancode_cmd)

    graph_dict, metadata = load_graph(graph_output)
    license_map = {k: _normalize_spdx_expression(v) for k, v in load_license_map(license_output).items()}

    context, results = check_compatibility(
        license_map,
        str(graph_output),
        args={"ignore_unk": True, "merge_cycles": False},
    )
    validation = _validate_alignment(graph_dict, license_map, context, logger)

    serialized_results = _jsonify(results)
    payload = {
        "metadata": metadata,
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
        "-t",
        "--third-party",
        action="store_true",
        help="Enable third-party dependency resolution and license scanning.",
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
