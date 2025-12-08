"""Global task pool for the new multiprocessing architecture.

This module provides a single global ProcessPoolExecutor that handles all
parallel task execution. It replaces both TransactionCoordinator and
CodeParserPool to eliminate nested process pools and prevent deadlocks.

Key features:
- Single process pool (no nesting)
- Explicit signal handling (SIGINT/SIGTERM)
- Task cancellation support
- Graceful shutdown with forced termination fallback
"""

from __future__ import annotations

import atexit
import logging
import multiprocessing
import os
import signal
import sys
import threading
import time
from concurrent.futures import (
    Future,
    ProcessPoolExecutor,
    as_completed,
    wait,
    FIRST_COMPLETED,
)
from typing import Any, Callable, Dict, List, Optional, Set

from depanalyzer.runtime.task_types import Task, TaskResult, TaskBatch
from depanalyzer.runtime.task_executor import execute_task

logger = logging.getLogger("depanalyzer.runtime.task_pool")


class GlobalTaskPool:
    """Single global process pool for all parallel task execution.

    This class implements the Singleton pattern to ensure only one process
    pool exists in the application. All task types (detection, parsing,
    fetching, etc.) are executed through this pool.

    Usage:
        pool = GlobalTaskPool.get_instance()
        future = pool.submit(task)
        result = future.result(timeout=60)
    """

    _instance: Optional["GlobalTaskPool"] = None
    _lock: threading.Lock = threading.Lock()

    def __init__(self, max_workers: Optional[int] = None) -> None:
        """Initialize the global task pool.

        Args:
            max_workers: Maximum number of worker processes.
                Defaults to CPU count.
        """
        self._owner_pid = os.getpid()
        self.max_workers = max_workers or os.cpu_count() or 4
        self._executor: Optional[ProcessPoolExecutor] = None
        self._futures: Dict[str, Future] = {}  # task_id -> Future
        self._shutdown_event = threading.Event()
        self._shutdown_lock = threading.Lock()
        self._original_sigint_handler = None
        self._original_sigterm_handler = None

        logger.info(
            "GlobalTaskPool initialized with %d max workers", self.max_workers
        )

    @classmethod
    def get_instance(cls, max_workers: Optional[int] = None) -> "GlobalTaskPool":
        """Get the singleton instance.

        Args:
            max_workers: Maximum workers (only used on first call).

        Returns:
            GlobalTaskPool: Global instance.
        """
        if cls._instance is None:
            with cls._lock:
                # Double-check locking
                if cls._instance is None:
                    cls._instance = cls(max_workers)

        instance = cls._instance

        # Detect fork: reset if PID changed
        current_pid = os.getpid()
        owner_pid = getattr(instance, "_owner_pid", current_pid)
        if owner_pid != current_pid:
            with cls._lock:
                logger.debug(
                    "Resetting GlobalTaskPool after fork (owner=%s, current=%s)",
                    owner_pid,
                    current_pid,
                )
                cls._instance = cls(max_workers)
                return cls._instance

        # Update max_workers if executor not started
        if (
            max_workers is not None
            and instance._executor is None
            and max_workers != instance.max_workers
        ):
            instance.max_workers = max_workers
            logger.info(
                "Reconfigured GlobalTaskPool max_workers to %d",
                instance.max_workers,
            )

        return instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (mainly for testing)."""
        with cls._lock:
            if cls._instance is not None:
                owner_pid = getattr(cls._instance, "_owner_pid", os.getpid())
                if owner_pid == os.getpid():
                    cls._instance.shutdown(wait=True)
            cls._instance = None
            cls._initialized = False

    def _create_executor(self) -> ProcessPoolExecutor:
        """Create ProcessPoolExecutor with appropriate start method."""
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
            "Process pool started with %d workers (start_method=%s)",
            self.max_workers,
            start_method,
        )
        return executor

    def _ensure_executor(self) -> ProcessPoolExecutor:
        """Ensure executor is created (lazy initialization).

        Note: ProcessPoolExecutor should ideally be created in the main thread
        on Windows to avoid potential issues with spawn mode.

        Returns:
            ProcessPoolExecutor: The process pool executor.
        """
        if self._executor is None:
            with self._shutdown_lock:
                # Double-check after acquiring lock
                if self._executor is None:
                    self._executor = self._create_executor()
                    self._setup_signal_handlers()
                    # Note: We don't register atexit handler here because it can
                    # cause hangs on Windows. The caller is responsible for
                    # calling shutdown() explicitly.
        return self._executor

    def _setup_signal_handlers(self) -> None:
        """Set up signal handlers for graceful shutdown."""
        # Only set up in main thread
        if threading.current_thread() is not threading.main_thread():
            return

        try:
            # Save original handlers
            self._original_sigint_handler = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, self._handle_signal)

            if hasattr(signal, "SIGTERM"):
                self._original_sigterm_handler = signal.getsignal(signal.SIGTERM)
                signal.signal(signal.SIGTERM, self._handle_signal)

            logger.debug("Signal handlers installed")
        except (ValueError, OSError) as e:
            # Signal handling may fail in some contexts (e.g., threads)
            logger.debug("Could not install signal handlers: %s", e)

    def _restore_signal_handlers(self) -> None:
        """Restore original signal handlers."""
        if threading.current_thread() is not threading.main_thread():
            return

        try:
            if self._original_sigint_handler is not None:
                signal.signal(signal.SIGINT, self._original_sigint_handler)
            if hasattr(signal, "SIGTERM") and self._original_sigterm_handler is not None:
                signal.signal(signal.SIGTERM, self._original_sigterm_handler)
        except (ValueError, OSError):
            pass

    def _handle_signal(self, signum: int, frame) -> None:
        """Handle shutdown signals (SIGINT/SIGTERM).

        Args:
            signum: Signal number.
            frame: Current stack frame.
        """
        sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        logger.warning("Received signal %s, initiating graceful shutdown", sig_name)

        self._shutdown_event.set()
        self.cancel_all()
        self.shutdown(wait=False, force=True)

        # Re-raise to allow default handling
        if self._original_sigint_handler and signum == signal.SIGINT:
            if callable(self._original_sigint_handler):
                self._original_sigint_handler(signum, frame)
            else:
                raise KeyboardInterrupt

    def _atexit_cleanup(self) -> None:
        """Cleanup handler for atexit."""
        try:
            self.shutdown(wait=False, force=True)
        except Exception:
            pass

    def submit(self, task: Task) -> Future:
        """Submit a task for execution.

        Args:
            task: Task to execute.

        Returns:
            Future: Future for tracking execution.
        """
        if self._shutdown_event.is_set():
            future: Future = Future()
            future.set_exception(RuntimeError("Task pool is shutting down"))
            return future

        executor = self._ensure_executor()

        # Submit task to process pool
        future = executor.submit(execute_task, task.to_dict())
        self._futures[task.task_id] = future

        logger.debug(
            "Submitted task %s (type=%s, priority=%d)",
            task.task_id,
            task.task_type.value,
            task.priority,
        )

        return future

    def submit_batch(self, batch: TaskBatch) -> Dict[str, Future]:
        """Submit a batch of tasks.

        Args:
            batch: Batch of tasks to submit.

        Returns:
            Dict[str, Future]: Map of task_id to Future.
        """
        futures = {}
        for task in batch:
            futures[task.task_id] = self.submit(task)
        return futures

    def wait_for_tasks(
        self,
        task_ids: List[str],
        timeout: Optional[float] = None,
    ) -> Dict[str, TaskResult]:
        """Wait for specific tasks to complete.

        Args:
            task_ids: List of task IDs to wait for.
            timeout: Optional timeout in seconds.

        Returns:
            Dict[str, TaskResult]: Results for each task.
        """
        results: Dict[str, TaskResult] = {}
        futures_to_wait = {
            tid: self._futures[tid]
            for tid in task_ids
            if tid in self._futures
        }

        if not futures_to_wait:
            return results

        deadline = time.time() + timeout if timeout else None

        try:
            # Build reverse mapping for O(1) lookup
            future_to_task = {f: tid for tid, f in futures_to_wait.items()}

            for future in as_completed(
                futures_to_wait.values(),
                timeout=timeout,
            ):
                task_id = future_to_task.get(future)
                if task_id is None:
                    continue

                try:
                    result_dict = future.result(timeout=0)
                    results[task_id] = TaskResult(
                        task_id=task_id,
                        success=result_dict.get("success", False),
                        result=result_dict.get("result"),
                        error=result_dict.get("error"),
                        execution_time=result_dict.get("execution_time", 0.0),
                    )
                except Exception as e:
                    results[task_id] = TaskResult(
                        task_id=task_id,
                        success=False,
                        error=str(e),
                    )

                # Remove from tracking
                self._futures.pop(task_id, None)

        except TimeoutError:
            # Handle tasks that didn't complete
            for task_id in task_ids:
                if task_id not in results:
                    results[task_id] = TaskResult(
                        task_id=task_id,
                        success=False,
                        error="Timeout waiting for task",
                    )

        return results

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a pending task.

        Args:
            task_id: ID of task to cancel.

        Returns:
            bool: True if task was cancelled.
        """
        future = self._futures.get(task_id)
        if future and not future.done():
            cancelled = future.cancel()
            if cancelled:
                logger.info("Cancelled task: %s", task_id)
            return cancelled
        return False

    def cancel_all(self) -> int:
        """Cancel all pending tasks.

        Returns:
            int: Number of tasks cancelled.
        """
        cancelled = 0
        for task_id, future in list(self._futures.items()):
            if not future.done():
                if future.cancel():
                    cancelled += 1
                    logger.debug("Cancelled task: %s", task_id)
        logger.info("Cancelled %d pending tasks", cancelled)
        return cancelled

    def is_shutting_down(self) -> bool:
        """Check if pool is shutting down.

        Returns:
            bool: True if shutdown is in progress.
        """
        return self._shutdown_event.is_set()

    def shutdown(self, wait: bool = True, force: bool = False) -> None:
        """Shutdown the process pool.

        Args:
            wait: Whether to wait for pending tasks.
            force: Force terminate stuck processes.
        """
        with self._shutdown_lock:
            if self._executor is None:
                return

            self._shutdown_event.set()
            executor = self._executor
            self._executor = None

            self._log_diagnostics("pre-shutdown")
            logger.info("Shutting down GlobalTaskPool (wait=%s, force=%s)", wait, force)

            # Restore signal handlers before shutdown
            self._restore_signal_handlers()

            # Shutdown executor - use wait=False to avoid blocking
            # Python's ProcessPoolExecutor.shutdown(wait=True) can hang
            # on Windows when workers are stuck in queue operations
            executor.shutdown(wait=False, cancel_futures=True)

            # Always force terminate on Windows to avoid hangs
            if wait or force:
                self._wait_for_processes(executor, timeout=5.0, force=True)

            self._futures.clear()
            logger.info("GlobalTaskPool shut down")

    def _wait_for_processes(
        self,
        executor: ProcessPoolExecutor,
        timeout: float = 5.0,
        force: bool = False,
    ) -> None:
        """Wait for worker processes to terminate.

        Args:
            executor: The executor to wait on.
            timeout: Maximum wait time.
            force: Force terminate if still alive.
        """
        processes = getattr(executor, "_processes", {}) or {}
        if not processes:
            return

        # On Windows, always force terminate to avoid hangs
        # ProcessPoolExecutor workers can get stuck waiting on queues
        if force or os.name == "nt":
            alive = [p for p in processes.values() if p.is_alive()]
            if alive:
                logger.info(
                    "Terminating %d worker process(es): %s",
                    len(alive),
                    ", ".join(str(getattr(p, "pid", "?")) for p in alive),
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

    def _log_diagnostics(self, when: str) -> None:
        """Log diagnostic information about the pool state.

        Args:
            when: Label for when diagnostics are being logged.
        """
        executor = self._executor
        if executor is None:
            logger.debug("GlobalTaskPool diagnostics (%s): executor is None", when)
            return

        try:
            pending = sum(1 for f in self._futures.values() if not f.done())
            done = len(self._futures) - pending
            logger.info(
                "GlobalTaskPool diagnostics (%s): futures=%d (pending=%d, done=%d)",
                when,
                len(self._futures),
                pending,
                done,
            )

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
                    "GlobalTaskPool diagnostics (%s): processes=%d [%s]",
                    when,
                    len(processes),
                    ", ".join(statuses),
                )
        except Exception as e:
            logger.debug("Diagnostics failed (%s): %s", when, e)

    def __enter__(self) -> "GlobalTaskPool":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.shutdown(wait=True)


# =============================================================================
# Convenience Functions
# =============================================================================

def get_task_pool(max_workers: Optional[int] = None) -> GlobalTaskPool:
    """Get the global task pool instance.

    Args:
        max_workers: Maximum workers (only used on first call).

    Returns:
        GlobalTaskPool: Global instance.
    """
    return GlobalTaskPool.get_instance(max_workers)


def submit_task(task: Task) -> Future:
    """Submit a task to the global pool.

    Args:
        task: Task to execute.

    Returns:
        Future: Future for tracking execution.
    """
    return get_task_pool().submit(task)


def shutdown_pool(wait: bool = True, force: bool = False) -> None:
    """Shutdown the global task pool.

    Args:
        wait: Whether to wait for pending tasks.
        force: Force terminate stuck processes.
    """
    pool = GlobalTaskPool._instance
    if pool is not None:
        pool.shutdown(wait=wait, force=force)
