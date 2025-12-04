"""Global process pool for parallel source code parsing.

Provides a singleton process pool for parsing source code files across all ecosystems.
Uses process-level parser caching to avoid repeated initialization overhead (especially
for tree-sitter parsers).

Unlike Transaction-level parallelism (which uses ProcessPoolExecutor for entire
transactions), this pool provides file-level parallelism for fine-grained code parsing.
"""

# Worker pool paths intentionally catch Exception to surface failures per file rather than crash.


import logging
import multiprocessing
import os
import time
from concurrent.futures import (
    BrokenExecutor,
    CancelledError,
    Future,
    ProcessPoolExecutor,
)
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from depanalyzer.parsers.registry import EcosystemRegistry

logger = logging.getLogger("depanalyzer.runtime.code_parser_pool")

# =============================================================================
# Configuration Constants
# =============================================================================

# Default timeout for parsing a single file (seconds)
DEFAULT_FILE_PARSE_TIMEOUT: float = 60.0

# Default timeout for batch parsing operations (seconds)
DEFAULT_BATCH_TIMEOUT: float = 300.0

# Cache TTL for parser instances (seconds) - parsers unused for this long are cleaned up
_CACHE_TTL: float = 300.0

# =============================================================================
# Process-level Parser Cache
# =============================================================================

# Process-level parser cache (per worker process)
# This avoids re-initializing expensive parsers (tree-sitter) for each file
_CODE_PARSER_CACHE: Dict[str, Any] = {}

# Timestamps for cache entries (for TTL-based cleanup)
_CACHE_TIMESTAMPS: Dict[str, float] = {}


def _cleanup_expired_cache() -> int:
    """Clean up expired parser cache entries based on TTL.

    Returns:
        int: Number of cache entries cleaned up.
    """
    now = time.time()
    expired = [k for k, t in _CACHE_TIMESTAMPS.items() if now - t > _CACHE_TTL]

    for key in expired:
        parser = _CODE_PARSER_CACHE.pop(key, None)
        _CACHE_TIMESTAMPS.pop(key, None)
        # Call cleanup method if parser supports it
        if parser and hasattr(parser, "cleanup"):
            try:
                parser.cleanup()
            except Exception:
                pass

    if expired:
        logger.debug(
            "[PID %d] Cleaned up %d expired parser cache entries: %s",
            os.getpid(),
            len(expired),
            ", ".join(expired),
        )

    return len(expired)


def clear_parser_cache() -> None:
    """Explicitly clear all parser cache entries.

    Call this to free memory when parsing is complete.
    """
    count = len(_CODE_PARSER_CACHE)
    for parser in _CODE_PARSER_CACHE.values():
        if hasattr(parser, "cleanup"):
            try:
                parser.cleanup()
            except Exception:
                pass
    _CODE_PARSER_CACHE.clear()
    _CACHE_TIMESTAMPS.clear()
    if count > 0:
        logger.debug("[PID %d] Cleared %d parser cache entries", os.getpid(), count)


def _get_code_parser(ecosystem: str, config: Any | None = None):
    """Get or create cached code parser instance for worker process.

    Reuses parser instances within worker processes to avoid repeated
    initialization overhead (especially for tree-sitter parsers).

    Args:
        ecosystem: Ecosystem identifier (cpp, hvigor, etc.).
        config: Optional configuration for the parser.

    Returns:
        BaseCodeParser instance or None if not found.
    """
    # Periodically clean up expired cache entries
    _cleanup_expired_cache()

    now = time.time()

    if ecosystem not in _CODE_PARSER_CACHE:
        try:
            registry = EcosystemRegistry.get_instance()
            parser_class = registry.get_code_parser(ecosystem)

            if parser_class is None:
                logger.warning("No code parser registered for ecosystem: %s", ecosystem)
                return None

            # Instantiate and cache; prefer passing through configuration
            # when provided and the parser supports it.
            try:
                if config is not None:
                    _CODE_PARSER_CACHE[ecosystem] = parser_class(config=config)
                else:
                    _CODE_PARSER_CACHE[ecosystem] = parser_class()
            except TypeError:
                # Fallback for parsers that do not accept a config argument
                _CODE_PARSER_CACHE[ecosystem] = parser_class()

            logger.debug(
                "[PID %d] Cached code parser for ecosystem: %s", os.getpid(), ecosystem
            )

        except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as e:
            logger.error("Failed to create code parser for %s: %s", ecosystem, e)
            return None

    # Update timestamp for TTL tracking
    _CACHE_TIMESTAMPS[ecosystem] = now

    return _CODE_PARSER_CACHE[ecosystem]


def _code_worker_dispatch(task_data: Tuple[str, str, Any | None]) -> Dict[str, Any]:
    """Worker process task dispatcher function.

    This function is the entry point for worker processes. It must be a
    top-level function (not a method) to be picklable.

    Args:
        task_data: Tuple containing (file_path, ecosystem).

    Returns:
        Dict[str, Any]: Parse result or skip/error result.
    """
    file_path_str, ecosystem, config = task_data
    file_path = Path(file_path_str)

    # Get cached parser instance for this worker process
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

        # Best-effort enrichment of result payload for downstream consumers
        if isinstance(result, dict):
            # Ensure original file path is present
            result.setdefault("file", file_path_str)
            # Propagate ecosystem so Transaction can specialize handling
            result.setdefault("ecosystem", ecosystem)
        return result
    except (OSError, ValueError, RuntimeError, UnicodeDecodeError) as e:
        logger.error("[PID %d] Failed to parse %s: %s", os.getpid(), file_path, e)
        return {
            "file": file_path_str,
            "error": str(e),
        }


class CodeParserPool:
    """Global singleton process pool for source code parsing.

    Manages a ProcessPoolExecutor that parses source code files in parallel
    across multiple processes. Provides process-level parser caching to avoid
    repeated initialization of expensive parsers (tree-sitter).

    This pool is shared across all Transactions, providing fine-grained
    file-level parallelism for code parsing.

    Usage:
        pool = CodeParserPool.get_instance()
        future = pool.submit_file(file_path, ecosystem)
        result = future.result()
    """

    _instance: Optional["CodeParserPool"] = None
    _initialized: bool = False

    def __new__(cls, *args, **kwargs):
        """Singleton pattern implementation.

        Args:
            *args: Positional arguments (ignored for singleton construction).
            **kwargs: Keyword arguments (ignored for singleton construction).

        Returns:
            CodeParserPool: Singleton instance.
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, max_workers: Optional[int] = None):
        """Initialize code parser pool.

        Args:
            max_workers: Maximum concurrent processes. Defaults to CPU count.
        """
        # Only initialize once
        if CodeParserPool._initialized:
            return

        self.max_workers = max_workers or os.cpu_count() or 4
        self._executor: Optional[ProcessPoolExecutor] = None

        logger.info("CodeParserPool initialized with %d max workers", self.max_workers)

        CodeParserPool._initialized = True

    @classmethod
    def get_instance(cls, max_workers: Optional[int] = None) -> "CodeParserPool":
        """Get the singleton instance.

        Args:
            max_workers: Maximum concurrent processes (only used on first call).

        Returns:
            CodeParserPool: Global instance.
        """
        if cls._instance is None:
            cls._instance = cls(max_workers)
            return cls._instance

        instance: "CodeParserPool" = cls._instance
        if max_workers is not None:
            requested = max_workers or os.cpu_count() or 4
            if instance._executor is None and requested != instance.max_workers:
                instance.max_workers = requested
                logger.info(
                    "Reconfigured CodeParserPool max_workers to %d",
                    instance.max_workers,
                )
            elif instance._executor is not None and requested != instance.max_workers:
                logger.debug(
                    "CodeParserPool already started with %d workers, ignoring new value %d",
                    instance.max_workers,
                    requested,
                )

        return instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (mainly for testing)."""
        if cls._instance is not None:
            cls._instance.shutdown()
        cls._instance = None
        cls._initialized = False

    def _create_executor(self) -> ProcessPoolExecutor:
        """Create a ProcessPoolExecutor with a POSIX-friendly start method."""
        executor_kwargs = {"max_workers": self.max_workers}
        start_method = "default"
        if os.name == "nt":
            executor_kwargs["mp_context"] = multiprocessing.get_context("spawn")
            start_method = "spawn"
        else:
            try:
                executor_kwargs["mp_context"] = multiprocessing.get_context("fork")
                start_method = "fork"
            except (ValueError, RuntimeError):
                start_method = (
                    multiprocessing.get_start_method(allow_none=True) or "default"
                )

        executor = ProcessPoolExecutor(**executor_kwargs)
        logger.info(
            "Process pool executor started with %d workers (start_method=%s)",
            self.max_workers,
            start_method,
        )
        return executor

    def _ensure_executor(self) -> ProcessPoolExecutor:
        """Ensure executor is created (lazy initialization).

        Returns:
            ProcessPoolExecutor: The process pool executor.
        """
        if self._executor is None:
            self._executor = self._create_executor()
        return self._executor

    def submit_file(
        self, file_path: Path, ecosystem: str, config: Any | None = None
    ) -> Future:
        """Submit a single file for parsing.

        Args:
            file_path: Path to source code file.
            ecosystem: Ecosystem identifier (cpp, hvigor, etc.).

        Returns:
            Future: Future object for tracking execution.
        """
        executor = self._ensure_executor()

        future = executor.submit(
            _code_worker_dispatch, (str(file_path), ecosystem, config)
        )

        logger.debug(
            "Submitted file for parsing: %s (ecosystem: %s)", file_path, ecosystem
        )

        return future

    def submit_batch(
        self, file_paths: List[Path], ecosystem: str, config: Any | None = None
    ) -> List[Tuple[Path, Future]]:
        """Submit multiple files for parsing in batch.

        Args:
            file_paths: List of source code file paths.
            ecosystem: Ecosystem identifier.

        Returns:
            List[Tuple[Path, Future]]: List of (file_path, future) tuples.
        """
        executor = self._ensure_executor()

        futures: List[Tuple[Path, Future]] = []
        for file_path in file_paths:
            future = executor.submit(
                _code_worker_dispatch, (str(file_path), ecosystem, config)
            )
            futures.append((file_path, future))

        logger.info(
            "Submitted batch of %d files for parsing (ecosystem: %s)",
            len(file_paths),
            ecosystem,
        )

        return futures

    def wait_for_completion(
        self,
        futures: List[Tuple[Path, Future]],
        timeout: Optional[float] = None,
        per_file_timeout: Optional[float] = None,
    ) -> Dict[Path, Dict[str, Any]]:
        """Wait for all futures to complete and collect results.

        Args:
            futures: List of (file_path, future) tuples.
            timeout: Optional total timeout in seconds (deprecated, use per_file_timeout).
            per_file_timeout: Timeout for each individual file parse operation.
                Defaults to DEFAULT_FILE_PARSE_TIMEOUT (60 seconds).

        Returns:
            Dict[Path, Dict[str, Any]]: Map of file_path to parse result.
        """
        results = {}
        # Use per_file_timeout if specified, otherwise fall back to timeout, then default
        file_timeout = per_file_timeout or timeout or DEFAULT_FILE_PARSE_TIMEOUT
        timed_out_count = 0

        for file_path, future in futures:
            try:
                result = future.result(timeout=file_timeout)
                results[file_path] = result
            except TimeoutError:
                timed_out_count += 1
                logger.error(
                    "Timeout parsing file %s after %.1fs", file_path, file_timeout
                )
                results[file_path] = {
                    "file": str(file_path),
                    "error": "timeout",
                    "error_type": "TimeoutError",
                    "timeout_seconds": file_timeout,
                }
                # Cancel the future to free resources
                future.cancel()
            except (CancelledError, BrokenExecutor, OSError, ValueError, RuntimeError) as e:
                logger.error("Failed to get result for %s: %s", file_path, e)
                results[file_path] = {
                    "file": str(file_path),
                    "error": str(e),
                    "error_type": type(e).__name__,
                }

        successful = sum(
            1 for r in results.values() if "error" not in r and "skipped" not in r
        )
        skipped = sum(1 for r in results.values() if r.get("skipped"))
        failed = len(results) - successful - skipped

        logger.info(
            "Code parsing completed: %d successful, %d skipped, %d failed (%d timed out)",
            successful,
            skipped,
            failed,
            timed_out_count,
        )

        return results

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the process pool.

        Args:
            wait: Whether to wait for pending tasks to complete.
        """
        if self._executor is not None:
            executor = self._executor
            self._log_executor_diagnostics("pre-shutdown")
            logger.info("Shutting down code parser pool (wait=%s)", wait)
            executor.shutdown(wait=False)
            if wait:
                self._wait_for_executor_processes(executor, timeout=10.0)
            self._executor = None
            logger.info("Code parser pool shut down")

    def _log_executor_diagnostics(self, when: str) -> None:
        """Log best-effort diagnostics for the pool to debug shutdown hangs."""
        executor = self._executor
        if executor is None:
            logger.debug("CodeParserPool diagnostics (%s): executor is None", when)
            return

        try:
            processes = getattr(executor, "_processes", {}) or {}
            if processes:
                statuses = []
                for pid, proc in processes.items():
                    alive = proc.is_alive()
                    exit_code = getattr(proc, "exitcode", None)
                    status = f"{pid}:{'alive' if alive else 'dead'}"
                    if exit_code is not None:
                        status = f"{status}/exit={exit_code}"
                    statuses.append(status)
                logger.info(
                    "CodeParserPool diagnostics (%s): processes=%d [%s]",
                    when,
                    len(processes),
                    ", ".join(statuses),
                )
            else:
                logger.info("CodeParserPool diagnostics (%s): processes=0", when)
        except Exception as exc:  # pragma: no cover - diagnostics only
            logger.debug(
                "CodeParserPool diagnostics (%s) failed: %s",
                when,
                exc,
            )

    def _wait_for_executor_processes(
        self, executor: ProcessPoolExecutor, timeout: float = 10.0
    ) -> None:
        """Best-effort wait for worker processes, with forced termination fallback."""
        processes = getattr(executor, "_processes", {}) or {}
        if not processes:
            return

        start = time.time()
        for proc in processes.values():
            remaining = timeout - (time.time() - start)
            if remaining <= 0:
                break
            try:
                proc.join(remaining)
            except (OSError, ValueError):
                logger.debug("Failed to join process %s", getattr(proc, "pid", "?"))

        alive = [proc for proc in processes.values() if proc.is_alive()]
        if not alive:
            return

        logger.warning(
            "Force-terminating %d stuck code parser worker(s): %s",
            len(alive),
            ", ".join(str(getattr(proc, "pid", "?")) for proc in alive),
        )
        for proc in alive:
            try:
                proc.terminate()
            except (OSError, ValueError) as exc:
                logger.debug(
                    "Failed to terminate process %s: %s",
                    getattr(proc, "pid", "?"),
                    exc,
                )

        for proc in alive:
            try:
                proc.join(timeout=1.0)
            except (OSError, ValueError):
                logger.debug(
                    "Failed to join terminated process %s", getattr(proc, "pid", "?")
                )

    def __enter__(self) -> "CodeParserPool":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.shutdown(wait=True)
