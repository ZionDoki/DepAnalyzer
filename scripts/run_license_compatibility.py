#!/usr/bin/env python3
"""Run depanalyzer scan + ScanCode + liscopelens compatibility end-to-end."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Set, Tuple, List, TYPE_CHECKING

if TYPE_CHECKING:
    from rich.progress import Progress, TaskID

# Ensure repository root is importable when running as a script.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Default locations expected in this workspace.
DEFAULT_PROJECT = Path("/mnt/c/Workspace/dialog_hub-master")
DEFAULT_LISCOPLENS_PATH = Path("/mnt/c/Workspace/liscopelens")

logger = logging.getLogger("depanalyzer.scripts.license_pipeline")


def _configure_logging(verbose: bool) -> None:
    """Configure logging, falling back gracefully when Rich is unavailable.

    Args:
        verbose: Whether to enable debug logging.

    Returns:
        None.
    """
    level = logging.DEBUG if verbose else logging.INFO
    try:
        from rich.logging import RichHandler
    except ImportError:
        logging.basicConfig(level=level, format="%(levelname)s: %(message)s")
        logger.warning("rich is not installed; using basic logging.")
        return

    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, markup=True)],
    )


def _create_progress() -> Optional["Progress"]:
    """Create a Rich progress instance when the dependency is available.

    Returns:
        Configured Rich progress object, or None if Rich is not installed.
    """
    try:
        from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
    except ImportError:
        logger.warning("rich is not installed; progress display disabled.")
        return None

    progress_columns = [
        SpinnerColumn(),
        TextColumn("[bold blue]{task.fields[project]}"),
        TextColumn("{task.fields[phase]}"),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
    ]
    return Progress(*progress_columns, transient=False)


def ensure_liscopelens_importable(liscopelens_path: Path) -> None:
    """Add liscopelens source tree to sys.path when it is not installed.

    Args:
        liscopelens_path: Path to the liscopelens repository.

    Returns:
        None.
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

    Returns:
        None.

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


def _update_task_phase(
    progress: Optional[Progress],
    task_id: Optional[TaskID],
    phase: str,
    advance: bool = False,
) -> None:
    """Update a Rich progress task phase safely when progress tracking is enabled.

    Args:
        progress: Rich progress instance or None when disabled.
        task_id: Task identifier to update.
        phase: Human-readable phase text.
        advance: Whether to advance the task counter.

    Returns:
        None.
    """
    if progress is None or task_id is None:
        return
    if advance:
        progress.advance(task_id)
    progress.update(task_id, phase=phase)


@contextmanager
def _suspend_progress(progress: Optional[Progress]) -> None:
    """Temporarily stop Rich progress to allow nested live displays to run.

    Args:
        progress: Progress instance to suspend.

    Returns:
        None.

    Yields:
        None: Execution continues after progress is suspended.
    """
    if progress is None:
        yield
        return

    was_started = getattr(getattr(progress, "live", None), "is_started", False)
    try:
        if was_started:
            progress.stop()
        yield
    finally:
        if was_started:
            progress.start()


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
    """Load aggregated node->license mapping from ScanCode output.

    Args:
        license_map_path: Path to the license map JSON file.

    Returns:
        Mapping of node identifiers to SPDX expressions.
    """
    with license_map_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, Mapping):
        raise ValueError(f"License map is not a mapping: {license_map_path}")
    return {str(k): str(v) for k, v in data.items()}


def _normalize_spdx_expression(expr: str) -> str:
    """Coerce license expressions to SPDX-friendly case (best effort).

    Args:
        expr: Raw license expression from ScanCode output.

    Returns:
        Normalized SPDX-like expression.
    """
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
    """Recursively convert sets and frozensets to sorted lists for JSON export.

    Args:
        obj: Arbitrary object tree to sanitize.

    Returns:
        JSON-serializable object.
    """
    if isinstance(obj, dict):
        return {k: _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, (set, frozenset)):
        return sorted(_jsonify(v) for v in obj)
    if isinstance(obj, list):
        return [_jsonify(v) for v in obj]
    return obj


def _derive_project_output_dir(
    base_dir: Path, project_path: Path, index: int, total: int
) -> Path:
    """Compute an output directory for a project, isolating batch runs when needed.

    Args:
        base_dir: Base output directory requested by the user.
        project_path: Path to the project being processed.
        index: 1-based project index in the batch.
        total: Total number of projects in the batch.

    Returns:
        Directory path where outputs for this project should be written.
    """
    resolved_base = base_dir.expanduser().resolve()
    if total <= 1:
        return resolved_base

    stem = project_path.name or project_path.expanduser().resolve().name
    safe_stem = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in stem)
    slug = f"{index:02d}_{safe_stem}"
    return resolved_base / slug


def _load_project_paths(args: argparse.Namespace) -> List[Path]:
    """Load and resolve project paths from CLI arguments.

    Args:
        args: Parsed command-line arguments.

    Returns:
        List of resolved project paths to process.
    """
    raw_paths: List[Path] = []
    if args.project_paths:
        raw_paths.extend(args.project_paths)

    if getattr(args, "projects_file", None):
        with args.projects_file.expanduser().open("r", encoding="utf-8") as handle:
            for line in handle:
                candidate = line.strip()
                if candidate:
                    raw_paths.append(Path(candidate))

    if not raw_paths:
        raw_paths.append(DEFAULT_PROJECT)

    seen: Set[Path] = set()
    resolved: List[Path] = []
    for path in raw_paths:
        resolved_path = path.expanduser().resolve()
        if resolved_path in seen:
            continue
        seen.add(resolved_path)
        resolved.append(resolved_path)
    return resolved


def _run_single_project(
    project_path: Path,
    args: argparse.Namespace,
    compatibility_runner: Any,
    graph_manager_cls: Any,
    output_dir: Path,
    progress: Optional[Progress],
    task_id: Optional[TaskID],
) -> Dict[str, Any]:
    """Execute scan → scancode → liscopelens compatibility pipeline for one project.

    Args:
        project_path: Repository path to analyze.
        args: Parsed command-line arguments.
        compatibility_runner: Callable to run liscopelens compatibility checks.
        graph_manager_cls: GraphManager class for saving compatibility graphs.
        output_dir: Destination directory for pipeline outputs.
        progress: Optional Rich progress instance.
        task_id: Optional progress task identifier.

    Returns:
        Dictionary summarizing outputs and validation metrics.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    graph_output = output_dir / "graph.json"
    license_output = output_dir / "license_map.json"
    compatibility_output = output_dir / "compatibility_results.json"
    compatibility_graph_output = output_dir / "compatibility_graph.json"

    _update_task_phase(progress, task_id, "scan")
    scan_cmd = [
        sys.executable,
        "-m",
        "depanalyzer.main",
        "scan",
        str(project_path),
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
    if not args.disable_fallback_tree:
        scan_cmd.append("--fallback-tree")

    run_command(scan_cmd)

    _update_task_phase(progress, task_id, "scancode", advance=True)
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
        str(project_path),
    ]
    if args.third_party:
        scancode_cmd.append("-t")
    if args.force_scancode:
        scancode_cmd.append("--force")

    run_command(scancode_cmd)

    _update_task_phase(progress, task_id, "compatibility", advance=True)
    graph_dict, metadata = load_graph(graph_output)
    license_map = {k: _normalize_spdx_expression(v) for k, v in load_license_map(license_output).items()}

    with _suspend_progress(progress):
        context, results = compatibility_runner(
            license_map,
            str(graph_output),
            args={"ignore_unk": True, "merge_cycles": True},
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

    if isinstance(context, graph_manager_cls):
        context.save(str(compatibility_graph_output))
        logger.info("Compatibility graph saved to %s", compatibility_graph_output)

    _update_task_phase(progress, task_id, "done", advance=True)
    return {
        "project": str(project_path),
        "output_dir": str(output_dir),
        "graph": str(graph_output),
        "license_map": str(license_output),
        "compatibility": str(compatibility_output),
        "validation": validation,
    }


def run_pipeline(args: argparse.Namespace) -> None:
    """Execute scan → scancode → liscopelens compatibility pipeline (batch capable).

    Args:
        args: Parsed command-line arguments.

    Returns:
        None.
    """
    ensure_liscopelens_importable(args.liscopelens_path)
    try:
        from liscopelens.api import check_compatibility  # pylint: disable=import-error
        from liscopelens.utils.graph import GraphManager  # pylint: disable=import-error
    except ImportError as exc:
        logger.error(
            "liscopelens is not installed. Install with `pip install liscopelens` "
            "or provide --liscopelens-path pointing to the source checkout."
        )
        raise RuntimeError("liscopelens is required for the license pipeline") from exc

    project_paths = _load_project_paths(args)
    output_base = args.output_dir.expanduser().resolve()
    output_base.mkdir(parents=True, exist_ok=True)

    summaries: List[Dict[str, Any]] = []
    failures: List[str] = []

    progress = _create_progress()

    if progress is None:
        for index, project_path in enumerate(project_paths, start=1):
            project_output_dir = _derive_project_output_dir(output_base, project_path, index, len(project_paths))
            try:
                summary = _run_single_project(
                    project_path,
                    args,
                    check_compatibility,
                    GraphManager,
                    project_output_dir,
                    None,
                    None,
                )
                summaries.append({**summary, "status": "ok"})
            except Exception as exc:  # pylint: disable=broad-except
                logger.error("Project %s failed: %s", project_path, exc)
                summaries.append(
                    {
                        "project": str(project_path),
                        "output_dir": str(project_output_dir),
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                failures.append(str(project_path))
    else:
        with progress:
            for index, project_path in enumerate(project_paths, start=1):
                project_output_dir = _derive_project_output_dir(output_base, project_path, index, len(project_paths))
                task_id = progress.add_task(
                    description=project_output_dir.name,
                    total=3,
                    project=str(project_path),
                    phase="queued",
                )
                try:
                    summary = _run_single_project(
                        project_path,
                        args,
                        check_compatibility,
                        GraphManager,
                        project_output_dir,
                        progress,
                        task_id,
                    )
                    summaries.append({**summary, "status": "ok"})
                except Exception as exc:  # pylint: disable=broad-except
                    _update_task_phase(progress, task_id, "failed")
                    logger.error("Project %s failed: %s", project_path, exc)
                    summaries.append(
                        {
                            "project": str(project_path),
                            "output_dir": str(project_output_dir),
                            "status": "failed",
                            "error": str(exc),
                        }
                    )
                    failures.append(str(project_path))

    if len(summaries) > 1:
        summary_path = output_base / "batch_summary.json"
        with summary_path.open("w", encoding="utf-8") as handle:
            json.dump(summaries, handle, indent=2, ensure_ascii=False)
        logger.info("Batch summary written to %s", summary_path)

    if failures:
        raise RuntimeError(f"One or more projects failed: {', '.join(failures)}")


def build_parser() -> argparse.ArgumentParser:
    """Build command-line argument parser.

    Returns:
        Configured ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        description="Run depanalyzer scan + scancode + liscopelens compatibility check."
    )
    parser.add_argument(
        "--project-path",
        dest="project_paths",
        action="append",
        type=Path,
        default=[],
        help=f"Repository to analyze; can be provided multiple times (default when omitted: {str(DEFAULT_PROJECT)}).",
    )
    parser.add_argument(
        "--projects-file",
        type=Path,
        help="File containing newline-separated project paths for batch scans.",
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
        help="Base output directory for graph, license map, and compatibility results (batch runs get per-project subdirectories).",
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
        "--disable-fallback-tree",
        action="store_true",
        help="Disable fallback license tree wiring (enabled by default in this pipeline).",
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
    """Entrypoint for the pipeline script.

    Returns:
        None.
    """
    parser = build_parser()
    args = parser.parse_args()

    _configure_logging(args.verbose)

    try:
        run_pipeline(args)
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Pipeline failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
