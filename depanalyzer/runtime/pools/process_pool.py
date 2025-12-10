"""Process-based task pool implementation.

This module provides ProcessTaskPool, a task pool that uses ProcessPoolExecutor
for CPU-intensive parallel task execution.

Key features:
- Zero-overhead design: executor created on first task, released when complete
- Event-driven result notification
- Platform-aware (spawn on Windows, fork on POSIX)
- Graceful shutdown with forced termination fallback
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import queue
import time
from concurrent.futures import Future, ProcessPoolExecutor, wait, FIRST_COMPLETED
from typing import Any, Callable, Dict, List, Optional, Tuple

from depanalyzer.runtime.pools.base import BaseTaskPool
from depanalyzer.runtime.task_types import Task, TaskResult

logger = logging.getLogger("depanalyzer.runtime.pools.process_pool")


class ProcessTaskPool(BaseTaskPool[Task, TaskResult]):
    """Process-based task pool for CPU-intensive tasks.

    This pool uses ProcessPoolExecutor for true parallel execution across
    multiple CPU cores. It follows a zero-overhead design where the executor
    is only created when tasks are submitted and released when all tasks
    complete.

    Attributes:
        max_workers: Maximum number of worker processes.
        name: Name of the pool for logging.
        execute_func: Function to execute tasks in worker processes.
    """

    def __init__(
        self,
        max_workers: Optional[int] = None,
        name: str = "ProcessTaskPool",
        execute_func: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> None:
        """Initialize the process task pool.

        Args:
            max_workers: Maximum number of worker processes. Defaults to CPU count.
            name: Name of the pool for logging.
            execute_func: Function to execute tasks. Must be picklable (top-level function).
                Defaults to execute_task from task_executor module.
        """
        workers = max_workers or os.cpu_count() or 4
        super().__init__(workers, name)

        self._executor: Optional[ProcessPoolExecutor] = None
        self._futures: Dict[str, Future] = {}
        self._future_to_task: Dict[Future, str] = {}

        # Import default execute function lazily to avoid circular imports
        if execute_func is None:
            from depanalyzer.runtime.task_executor import execute_task
            self._execute_func = execute_task
        else:
            self._execute_func = execute_func

    def _create_executor(self) -> ProcessPoolExecutor:
        """Create ProcessPoolExecutor with appropriate start method.

        Returns:
            ProcessPoolExecutor instance.
        """
        executor_kwargs = {"max_workers": self.max_workers}
        start_method = "default"

        if os.name == "nt":
            # Windows: must use spawn
            executor_kwargs["mp_context"] = multiprocessing.get_context("spawn")
            start_method = "spawn"
        else:
            # POSIX: prefer fork for performance
            try:
                executor_kwargs["mp_context"] = multiprocessing.get_context("fork")
                start_method = "fork"
            except (ValueError, RuntimeError):
                start_method = (
                    multiprocessing.get_start_method(allow_none=True) or "default"
                )

        executor = ProcessPoolExecutor(**executor_kwargs)
        logger.info(
            "%s: Process pool created with %d workers (start_method=%s)",
            self.name,
            self.max_workers,
            start_method,
        )
        return executor

    def _worker_loop(self) -> None:
        """Main loop for background worker thread.

        This method:
        1. Creates the executor on first task
        2. Submits tasks from task_queue to executor
        3. Collects results and puts them in result_queue
        4. Sets events for result availability and completion
        5. Cleans up executor on shutdown

        Uses wait(FIRST_COMPLETED) for efficient blocking instead of polling.
        """
        self._executor = None
        active_futures: List[Future] = []

        try:
            while not self._shutdown_event.is_set():
                # Submit pending tasks
                tasks_submitted = self._submit_pending_tasks(active_futures)

                if active_futures:
                    # Use wait() to efficiently block until at least one task completes
                    # This avoids busy-waiting and CPU spinning
                    done, _ = wait(active_futures, timeout=0.5, return_when=FIRST_COMPLETED)

                    # Process completed futures
                    for future in done:
                        self._process_completed_future(future, active_futures)

                    # Check completion
                    self._check_completion()
                else:
                    # No active futures - check if we should exit
                    if self.is_sealed() and self.get_active_count() == 0:
                        break
                    # Wait for new tasks or shutdown
                    self._shutdown_event.wait(timeout=0.1)

            # Process remaining tasks before shutdown
            self._drain_remaining_tasks(active_futures)

        except Exception as e:
            logger.error("%s worker loop error: %s", self.name, e, exc_info=True)

        finally:
            self._cleanup_executor()

    def _submit_pending_tasks(self, active_futures: List[Future]) -> int:
        """Submit pending tasks from queue to executor.

        Args:
            active_futures: List to append new futures to.

        Returns:
            Number of tasks submitted.
        """
        submitted = 0

        while True:
            try:
                task = self._task_queue.get_nowait()

                if task is None:
                    # Sentinel received - no more tasks
                    break

                # Lazy executor creation
                if self._executor is None:
                    self._executor = self._create_executor()

                # Submit task to executor
                future = self._executor.submit(self._execute_func, task.to_dict())
                self._futures[task.task_id] = future
                self._future_to_task[future] = task.task_id
                active_futures.append(future)
                submitted += 1

                logger.debug(
                    "%s: Submitted task %s (type=%s)",
                    self.name,
                    task.task_id,
                    task.task_type.value,
                )

            except queue.Empty:
                break

        return submitted

    def _process_completed_future(self, future: Future, active_futures: List[Future]) -> None:
        """Process a single completed future.

        Args:
            future: The completed future to process.
            active_futures: List of active futures to remove from.
        """
        task_id = self._future_to_task.get(future, "unknown")

        try:
            result_dict = future.result(timeout=0)
            result = TaskResult(
                task_id=task_id,
                success=result_dict.get("success", False),
                result=result_dict.get("result"),
                error=result_dict.get("error"),
                execution_time=result_dict.get("execution_time", 0.0),
            )
        except Exception as e:
            result = TaskResult(
                task_id=task_id,
                success=False,
                error=str(e),
            )

        # Put result in queue and notify
        self._result_queue.put(result)
        self._result_available_event.set()

        # Update tracking
        self._decrement_pending()
        self._futures.pop(task_id, None)
        self._future_to_task.pop(future, None)

        # Remove from active list
        if future in active_futures:
            active_futures.remove(future)

        logger.debug(
            "%s: Task %s completed (success=%s)",
            self.name,
            task_id,
            result.success,
        )

    def _drain_remaining_tasks(self, active_futures: List[Future]) -> None:
        """Wait for and collect all remaining active tasks.

        Args:
            active_futures: List of active futures to drain.
        """
        # Submit any remaining tasks in queue
        self._submit_pending_tasks(active_futures)

        # Wait for all active futures to complete using wait()
        timeout_total = 60.0
        deadline = time.time() + timeout_total

        while active_futures and time.time() < deadline:
            remaining_time = max(0.1, deadline - time.time())
            done, _ = wait(active_futures, timeout=min(remaining_time, 1.0), return_when=FIRST_COMPLETED)

            for future in done:
                self._process_completed_future(future, active_futures)

        # Handle any tasks that didn't complete
        for future in list(active_futures):
            task_id = self._future_to_task.get(future, "unknown")
            if not future.done():
                future.cancel()
                result = TaskResult(
                    task_id=task_id,
                    success=False,
                    error="Task cancelled during shutdown",
                )
                self._result_queue.put(result)
                self._result_available_event.set()
                self._decrement_pending()
            active_futures.remove(future)

        self._check_completion()

    def _cleanup_executor(self) -> None:
        """Clean up the executor and release resources."""
        if self._executor is None:
            return

        logger.debug("%s: Cleaning up executor", self.name)

        try:
            # Shutdown executor
            self._executor.shutdown(wait=False, cancel_futures=True)

            # Force terminate any stuck processes on Windows
            if os.name == "nt":
                self._terminate_processes()

        except Exception as e:
            logger.debug("%s: Executor cleanup error: %s", self.name, e)

        finally:
            self._executor = None
            self._futures.clear()
            self._future_to_task.clear()

    def _terminate_processes(self) -> None:
        """Force terminate worker processes (Windows workaround)."""
        if self._executor is None:
            return

        processes = getattr(self._executor, "_processes", {}) or {}
        alive = [p for p in processes.values() if p.is_alive()]

        if alive:
            logger.debug(
                "%s: Terminating %d worker process(es)",
                self.name,
                len(alive),
            )
            for proc in alive:
                try:
                    proc.terminate()
                except (OSError, ValueError):
                    pass

            # Brief wait for termination
            for proc in alive:
                try:
                    proc.join(timeout=1.0)
                except (OSError, ValueError):
                    pass
