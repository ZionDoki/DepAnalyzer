"""Code parser pool for PARSE_CODE tasks.

This module provides CodeParserPool, a specialized pool for CPU-intensive
source code parsing tasks using tree-sitter and other parsers.

Key features:
- Optimized for high-volume code parsing
- Tree-sitter parser caching in worker processes
- Convenience methods for file submission
- Real-time result streaming via ResultIterator
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from depanalyzer.runtime.pools.process_pool import ProcessTaskPool
from depanalyzer.runtime.task_types import Task, TaskType, create_task


class CodeParserPool(ProcessTaskPool):
    """Specialized pool for PARSE_CODE tasks.

    This pool is optimized for high-volume, CPU-intensive code parsing.
    It uses ProcessPoolExecutor for true parallel execution and benefits
    from tree-sitter parser caching in worker processes.

    Usage:
        pool = CodeParserPool(max_workers=4)
        pool.start()

        for file_path in code_files:
            pool.submit_file(file_path, ecosystem="cpp")
        pool.seal()

        for result in pool.results():
            if result.success:
                process_parse_result(result.result)
            else:
                handle_error(result.error)

    Attributes:
        max_workers: Maximum number of worker processes.
    """

    def __init__(self, max_workers: Optional[int] = None) -> None:
        """Initialize the code parser pool.

        Args:
            max_workers: Maximum number of worker processes.
                Defaults to CPU count.
        """
        workers = max_workers or os.cpu_count() or 4
        super().__init__(workers, name="CodeParserPool")

    def submit_file(
        self,
        file_path: Path,
        ecosystem: str,
        config: Optional[Dict[str, Any]] = None,
        timeout: float = 60.0,
    ) -> str:
        """Submit a code file for parsing.

        Convenience method that creates a PARSE_CODE task and submits it.

        Args:
            file_path: Path to the source code file.
            ecosystem: Ecosystem identifier (e.g., "cpp", "npm", "maven").
            config: Optional parser configuration.
            timeout: Task timeout in seconds.

        Returns:
            Task ID for tracking.

        Raises:
            RuntimeError: If the pool is sealed.
        """
        task = create_task(
            TaskType.PARSE_CODE,
            payload={
                "file_path": str(file_path),
                "ecosystem": ecosystem,
                "config": config,
            },
            timeout=timeout,
        )
        self.submit(task)
        return task.task_id

    def submit_files(
        self,
        file_paths: list,
        ecosystem: str,
        config: Optional[Dict[str, Any]] = None,
        timeout: float = 60.0,
    ) -> list:
        """Submit multiple code files for parsing.

        Convenience method for batch file submission.

        Args:
            file_paths: List of paths to source code files.
            ecosystem: Ecosystem identifier.
            config: Optional parser configuration.
            timeout: Task timeout in seconds.

        Returns:
            List of task IDs for tracking.

        Raises:
            RuntimeError: If the pool is sealed.
        """
        task_ids = []
        for file_path in file_paths:
            task_id = self.submit_file(file_path, ecosystem, config, timeout)
            task_ids.append(task_id)
        return task_ids
