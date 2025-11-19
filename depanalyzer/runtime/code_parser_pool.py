"""Global process pool for parallel source code parsing.

Provides a singleton process pool for parsing source code files across all ecosystems.
Uses process-level parser caching to avoid repeated initialization overhead (especially
for tree-sitter parsers).

Unlike Transaction-level parallelism (which uses ProcessPoolExecutor for entire
transactions), this pool provides file-level parallelism for fine-grained code parsing.
"""

# Worker pool paths intentionally catch Exception to surface failures per file rather than crash.
# pylint: disable=broad-exception-caught

import logging
import os
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("depanalyzer.runtime.code_parser_pool")

# Process-level parser cache (per worker process)
# This avoids re-initializing expensive parsers (tree-sitter) for each file
_CODE_PARSER_CACHE: Dict[str, Any] = {}


def _get_code_parser(ecosystem: str, config: Any | None = None):
    """Get or create cached code parser instance for worker process.

    Reuses parser instances within worker processes to avoid repeated
    initialization overhead (especially for tree-sitter parsers).

    Args:
        ecosystem: Ecosystem identifier (cpp, hvigor, etc.).

    Returns:
        BaseCodeParser instance or None if not found.
    """
    if ecosystem not in _CODE_PARSER_CACHE:
        try:
            # Import here to avoid circular dependencies
            from depanalyzer.parsers.registry import EcosystemRegistry

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

        except Exception as e:
            logger.error("Failed to create code parser for %s: %s", ecosystem, e)
            return None

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
    except Exception as e:
        logger.error(
            "[PID %d] Failed to parse %s: %s", os.getpid(), file_path, e
        )
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

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (mainly for testing)."""
        if cls._instance is not None:
            cls._instance.shutdown()
        cls._instance = None
        cls._initialized = False

    def _ensure_executor(self) -> ProcessPoolExecutor:
        """Ensure executor is created (lazy initialization).

        Returns:
            ProcessPoolExecutor: The process pool executor.
        """
        if self._executor is None:
            self._executor = ProcessPoolExecutor(max_workers=self.max_workers)
            logger.info(
                "Process pool executor started with %d workers", self.max_workers
            )
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
        self, futures: List[Tuple[Path, Future]], timeout: Optional[float] = None
    ) -> Dict[Path, Dict[str, Any]]:
        """Wait for all futures to complete and collect results.

        Args:
            futures: List of (file_path, future) tuples.
            timeout: Optional timeout in seconds.

        Returns:
            Dict[Path, Dict[str, Any]]: Map of file_path to parse result.
        """
        results = {}

        try:
            for file_path, future in futures:
                try:
                    result = future.result(timeout=timeout)
                    results[file_path] = result
                except Exception as e:
                    logger.error("Failed to get result for %s: %s", file_path, e)
                    results[file_path] = {
                        "file": str(file_path),
                        "error": str(e),
                    }

            successful = sum(
                1 for r in results.values() if "error" not in r and "skipped" not in r
            )
            failed = len(results) - successful

            logger.info(
                "Code parsing completed: %d successful, %d failed", successful, failed
            )

            return results

        except TimeoutError:
            logger.error("Timeout waiting for code parsing to complete")
            raise

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the process pool.

        Args:
            wait: Whether to wait for pending tasks to complete.
        """
        if self._executor is not None:
            logger.info("Shutting down code parser pool (wait=%s)", wait)
            self._executor.shutdown(wait=wait)
            self._executor = None
            logger.info("Code parser pool shut down")

    def __enter__(self) -> "CodeParserPool":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.shutdown(wait=True)
