"""Task executor for the global task pool architecture.

This module contains the worker-side execution logic for all task types.
All functions here must be picklable (top-level functions, not methods)
and should be stateless to safely execute in worker processes.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from depanalyzer.runtime.task_types import TaskType

logger = logging.getLogger("depanalyzer.runtime.task_executor")

# =============================================================================
# Process-level Caches (per worker process)
# =============================================================================

# Parser cache to avoid repeated initialization (especially tree-sitter)
_CODE_PARSER_CACHE: Dict[str, Any] = {}
_CACHE_TIMESTAMPS: Dict[str, float] = {}
_CACHE_TTL: float = 300.0  # 5 minutes
_MAX_CACHE_SIZE: int = 50


def _cleanup_expired_cache() -> int:
    """Clean up expired parser cache entries.

    Returns:
        int: Number of entries cleaned up.
    """
    now = time.time()
    cleaned = 0

    # Remove expired entries
    expired = [k for k, t in _CACHE_TIMESTAMPS.items() if now - t > _CACHE_TTL]
    for key in expired:
        parser = _CODE_PARSER_CACHE.pop(key, None)
        _CACHE_TIMESTAMPS.pop(key, None)
        if parser and hasattr(parser, "cleanup"):
            try:
                parser.cleanup()
            except Exception:
                pass
        cleaned += 1

    # Enforce max size (LRU eviction)
    if len(_CODE_PARSER_CACHE) > _MAX_CACHE_SIZE:
        sorted_keys = sorted(
            _CACHE_TIMESTAMPS.keys(),
            key=lambda k: _CACHE_TIMESTAMPS.get(k, 0)
        )
        excess = len(_CODE_PARSER_CACHE) - _MAX_CACHE_SIZE
        for key in sorted_keys[:excess]:
            parser = _CODE_PARSER_CACHE.pop(key, None)
            _CACHE_TIMESTAMPS.pop(key, None)
            if parser and hasattr(parser, "cleanup"):
                try:
                    parser.cleanup()
                except Exception:
                    pass
            cleaned += 1

    return cleaned


def _get_code_parser(ecosystem: str, config: Any = None):
    """Get or create cached code parser instance.

    Args:
        ecosystem: Ecosystem identifier.
        config: Optional parser configuration.

    Returns:
        BaseCodeParser instance or None.
    """
    _cleanup_expired_cache()

    now = time.time()

    if ecosystem not in _CODE_PARSER_CACHE:
        try:
            from depanalyzer.parsers.registry import EcosystemRegistry

            registry = EcosystemRegistry.get_instance()
            parser_class = registry.get_code_parser(ecosystem)

            if parser_class is None:
                logger.warning("No code parser for ecosystem: %s", ecosystem)
                return None

            # Instantiate parser
            try:
                if config is not None:
                    _CODE_PARSER_CACHE[ecosystem] = parser_class(config=config)
                else:
                    _CODE_PARSER_CACHE[ecosystem] = parser_class()
            except TypeError:
                _CODE_PARSER_CACHE[ecosystem] = parser_class()

            logger.debug(
                "[PID %d] Cached code parser for: %s", os.getpid(), ecosystem
            )

        except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as e:
            logger.error("Failed to create code parser for %s: %s", ecosystem, e)
            return None

    _CACHE_TIMESTAMPS[ecosystem] = now
    return _CODE_PARSER_CACHE[ecosystem]


# =============================================================================
# Main Task Dispatcher
# =============================================================================

def execute_task(task_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a single task in a worker process.

    This is the main entry point for worker processes. It dispatches
    to the appropriate handler based on task type.

    Args:
        task_dict: Task dictionary with keys:
            - task_type: TaskType value string
            - payload: Task-specific data
            - timeout: Task timeout in seconds

    Returns:
        Dict[str, Any]: Task result with keys:
            - success: bool
            - result: Task output (if successful)
            - error: Error message (if failed)
    """
    task_type_str = task_dict.get("task_type")
    payload = task_dict.get("payload", {})
    task_id = task_dict.get("task_id", "unknown")

    start_time = time.time()

    try:
        task_type = TaskType(task_type_str)

        if task_type == TaskType.DETECT_ECOSYSTEM:
            result = _execute_detect_ecosystem(payload)
        elif task_type == TaskType.PARSE_CONFIG:
            result = _execute_parse_config(payload)
        elif task_type == TaskType.PARSE_CODE:
            result = _execute_parse_code(payload)
        elif task_type == TaskType.FETCH_DEPENDENCY:
            result = _execute_fetch_dependency(payload)
        elif task_type == TaskType.ANALYZE_GRAPH:
            result = _execute_analyze_graph(payload)
        elif task_type == TaskType.EXPORT_GRAPH:
            result = _execute_export_graph(payload)
        else:
            return {
                "success": False,
                "error": f"Unknown task type: {task_type_str}",
                "execution_time": time.time() - start_time,
            }

        return {
            "success": True,
            "result": result,
            "execution_time": time.time() - start_time,
        }

    except Exception as e:
        logger.error(
            "[PID %d] Task %s failed: %s",
            os.getpid(),
            task_id,
            e,
            exc_info=True,
        )
        return {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
            "execution_time": time.time() - start_time,
        }


# =============================================================================
# Task Type Handlers
# =============================================================================

def _execute_detect_ecosystem(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Execute ecosystem detection task.

    Args:
        payload: Dict with keys:
            - ecosystem: Ecosystem identifier
            - workspace_root: Path to workspace root

    Returns:
        Dict with detected file paths.
    """
    from depanalyzer.parsers.registry import EcosystemRegistry
    from depanalyzer.runtime.eventbus import EventBus

    ecosystem = payload["ecosystem"]
    workspace_root = Path(payload["workspace_root"])
    config = payload.get("config")

    registry = EcosystemRegistry.get_instance()
    detector_class = registry.get_detector(ecosystem)

    if detector_class is None:
        return {
            "ecosystem": ecosystem,
            "detected": [],
            "error": f"No detector for ecosystem: {ecosystem}",
        }

    # Create a local event bus (events won't propagate to main process)
    eventbus = EventBus()

    try:
        detector = detector_class(
            workspace_root=workspace_root,
            eventbus=eventbus,
            config=config,
        )
        detected_paths = detector.detect()

        return {
            "ecosystem": ecosystem,
            "detected": [str(p) for p in detected_paths],
            "count": len(detected_paths),
        }

    except Exception as e:
        logger.error("Detection failed for %s: %s", ecosystem, e)
        return {
            "ecosystem": ecosystem,
            "detected": [],
            "error": str(e),
        }


def _execute_parse_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Execute configuration file parsing task.

    Args:
        payload: Dict with keys:
            - ecosystem: Ecosystem identifier
            - target_path: Path to config file
            - workspace_root: Path to workspace root

    Returns:
        Dict with parse results.
    """
    from depanalyzer.parsers.registry import EcosystemRegistry
    from depanalyzer.runtime.eventbus import EventBus
    from depanalyzer.graph import GraphManager

    ecosystem = payload["ecosystem"]
    target_path = Path(payload["target_path"])
    workspace_root = Path(payload["workspace_root"])
    config = payload.get("config")

    registry = EcosystemRegistry.get_instance()
    parser_class = registry.get_parser(ecosystem)

    if parser_class is None:
        return {
            "target_path": str(target_path),
            "error": f"No parser for ecosystem: {ecosystem}",
        }

    # Create local graph manager and event bus
    eventbus = EventBus()
    graph_manager = GraphManager()

    try:
        parser = parser_class(
            workspace_root=workspace_root,
            graph_manager=graph_manager,
            eventbus=eventbus,
            config=config,
        )
        parser.parse(target_path)

        # Extract graph data for serialization
        return {
            "target_path": str(target_path),
            "ecosystem": ecosystem,
            "node_count": graph_manager.node_count,
            "edge_count": graph_manager.edge_count,
            # Serialize graph data for aggregation in main process
            "nodes": list(graph_manager.nodes(data=True)),
            "edges": list(graph_manager.edges(data=True)),
            "code_files": [str(p) for p in parser.discover_code_files()],
        }

    except Exception as e:
        logger.error("Parse failed for %s: %s", target_path, e)
        return {
            "target_path": str(target_path),
            "error": str(e),
        }


def _execute_parse_code(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Execute source code file parsing task.

    Args:
        payload: Dict with keys:
            - file_path: Path to source file
            - ecosystem: Ecosystem identifier
            - config: Optional parser config

    Returns:
        Dict with parse results (imports, exports, etc.).
    """
    file_path_str = payload["file_path"]
    ecosystem = payload["ecosystem"]
    config = payload.get("config")

    file_path = Path(file_path_str)

    # Get cached parser
    parser = _get_code_parser(ecosystem, config=config)

    if parser is None:
        return {
            "file": file_path_str,
            "skipped": True,
            "reason": f"No code parser for ecosystem: {ecosystem}",
        }

    # Check if parser can handle this file
    if not parser.can_handle_file(file_path):
        return {
            "file": file_path_str,
            "skipped": True,
            "reason": "Unsupported file extension",
        }

    # Parse the file
    try:
        result = parser.parse_file(file_path)

        # Ensure result has required fields
        if isinstance(result, dict):
            result.setdefault("file", file_path_str)
            result.setdefault("ecosystem", ecosystem)

        return result

    except (OSError, ValueError, RuntimeError, UnicodeDecodeError) as e:
        logger.error("[PID %d] Failed to parse %s: %s", os.getpid(), file_path, e)
        return {
            "file": file_path_str,
            "error": str(e),
        }


def _execute_fetch_dependency(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Execute dependency fetching task.

    Args:
        payload: Dict with keys:
            - name: Dependency name
            - version: Version string
            - ecosystem: Ecosystem identifier
            - source_url: Optional source URL
            - metadata: Optional metadata dict
            - cache_root: Cache directory path

    Returns:
        Dict with fetch results.
    """
    from depanalyzer.parsers.base import DependencySpec
    from depanalyzer.parsers.registry import EcosystemRegistry

    name = payload["name"]
    version = payload.get("version")
    ecosystem = payload["ecosystem"]
    source_url = payload.get("source_url")
    metadata = payload.get("metadata", {})
    cache_root = Path(payload.get("cache_root", ".dep_cache"))

    # Create dependency spec
    dep_spec = DependencySpec(
        name=name,
        version=version,
        source_url=source_url,
        ecosystem=ecosystem,
        metadata=metadata,
    )

    # Get fetcher
    registry = EcosystemRegistry.get_instance()
    fetcher_class = registry.get_dep_fetcher(ecosystem)

    if fetcher_class is None:
        return {
            "name": name,
            "version": version,
            "ecosystem": ecosystem,
            "success": False,
            "error": f"No fetcher for ecosystem: {ecosystem}",
        }

    try:
        fetcher = fetcher_class(cache_root=cache_root)

        if not fetcher.can_handle(dep_spec):
            return {
                "name": name,
                "version": version,
                "ecosystem": ecosystem,
                "success": False,
                "error": "Fetcher cannot handle this dependency",
            }

        local_path = fetcher.fetch(dep_spec)

        if local_path:
            return {
                "name": name,
                "version": version,
                "ecosystem": ecosystem,
                "success": True,
                "local_path": str(local_path),
            }
        else:
            return {
                "name": name,
                "version": version,
                "ecosystem": ecosystem,
                "success": False,
                "error": "Fetch returned None",
            }

    except Exception as e:
        logger.error("Fetch failed for %s@%s: %s", name, version, e)
        return {
            "name": name,
            "version": version,
            "ecosystem": ecosystem,
            "success": False,
            "error": str(e),
        }


def _execute_analyze_graph(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Execute graph analysis task.

    Args:
        payload: Dict with analysis parameters.

    Returns:
        Dict with analysis results.
    """
    # Graph analysis is typically done in the main process
    # This is a placeholder for future distributed analysis
    analysis_type = payload.get("analysis_type", "basic")

    return {
        "analysis_type": analysis_type,
        "status": "not_implemented",
        "message": "Graph analysis runs in main process",
    }


def _execute_export_graph(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Execute graph export task.

    Args:
        payload: Dict with export parameters.

    Returns:
        Dict with export results.
    """
    # Graph export is typically done in the main process
    # This is a placeholder for future distributed export
    export_format = payload.get("format", "json")

    return {
        "format": export_format,
        "status": "not_implemented",
        "message": "Graph export runs in main process",
    }
