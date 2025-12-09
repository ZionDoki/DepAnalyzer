"""Concurrent task executor with queue-based scheduling.

Worker provides unified task queue management with deduplication,
rate limiting, and failure handling. All phases use the queue for
execution, avoiding recursion.
"""

import logging
import os
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass, field
from enum import Enum, auto
from queue import Empty, PriorityQueue
from typing import Any, Callable, Dict, Optional, Set, Tuple

logger = logging.getLogger("depanalyzer.worker")


class TaskPriority(Enum):
    """Task priority levels for scheduling."""

    HIGH = auto()
    NORMAL = auto()
    LOW = auto()


@dataclass
class RetryConfig:
    """Configuration for task retry behavior.

    Attributes:
        max_retries: Maximum number of retry attempts (0 = no retries).
        base_delay: Initial delay between retries in seconds.
        max_delay: Maximum delay between retries in seconds.
        exponential_base: Base for exponential backoff calculation.
        retryable_exceptions: Tuple of exception types that should trigger retry.
    """

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    exponential_base: float = 2.0
    retryable_exceptions: tuple = (OSError, TimeoutError, ConnectionError)


# Default retry config for tasks that don't specify one
DEFAULT_RETRY_CONFIG = RetryConfig(max_retries=0)  # No retries by default


@dataclass
class Task:
    """Task representation for worker queue.

    Attributes:
        task_id: Unique task identifier.
        func: Callable to execute.
        priority: Task priority level.
        metadata: Optional task metadata.
        retry_config: Optional retry configuration.
    """

    task_id: str
    func: Callable[[], Any]
    priority: TaskPriority = TaskPriority.NORMAL
    metadata: Dict[str, Any] = field(default_factory=dict)
    retry_config: Optional[RetryConfig] = None

    def __hash__(self) -> int:
        """Hash based on task_id for deduplication.

        Returns:
            int: Hash value.
        """
        return hash(self.task_id)

    def __eq__(self, other: object) -> bool:
        """Equality based on task_id.

        Args:
            other: Other task.

        Returns:
            bool: True if task_ids match.
        """
        if not isinstance(other, Task):
            return False
        return self.task_id == other.task_id


@dataclass
class TaskResult:
    """Task execution result.

    Attributes:
        task_id: Task identifier.
        success: Whether execution succeeded.
        result: Return value from task function.
        error: Exception if task failed.
        execution_time: Time taken in seconds.
    """

    task_id: str
    success: bool
    result: Any = None
    error: Optional[Exception] = None
    execution_time: float = 0.0


@dataclass(order=True)
class PrioritizedTask:
    """Wrapper for tasks with priority ordering.

    Used with PriorityQueue to ensure proper ordering.
    Lower priority values are processed first.
    """

    priority: int
    sequence: int  # Tie-breaker for FIFO within same priority
    task: Task = field(compare=False)


class Worker:
    """Concurrent task executor with priority queue-based scheduling.

    Worker manages a task queue with fingerprint-based deduplication,
    priority-based scheduling using PriorityQueue, and configurable parallelism.
    """

    # Priority mapping: lower values = higher priority
    _PRIORITY_MAP = {
        TaskPriority.HIGH: 0,
        TaskPriority.NORMAL: 1,
        TaskPriority.LOW: 2,
    }

    def __init__(self, max_workers: int = 8) -> None:
        """Initialize worker with thread pool.

        Args:
            max_workers: Maximum concurrent threads.
        """
        self.max_workers = max_workers
        self._queue: PriorityQueue[PrioritizedTask] = PriorityQueue()
        self._seen_tasks: Set[str] = set()
        self._seen_lock = threading.Lock()  # Separate lock for seen_tasks
        self._results: Dict[str, TaskResult] = {}
        self._results_lock = threading.Lock()  # Separate lock for results
        self._executor: Optional[ThreadPoolExecutor] = None
        self._sequence = 0  # Monotonic counter for FIFO ordering
        self._sequence_lock = threading.Lock()
        self._idle_wait_seconds = 0.5
        
        # Condition variable for task notifications (eliminates busy waiting)
        self._task_available = threading.Condition()

        logger.info(
            "[PID %d] Worker initialized with %d max workers", os.getpid(), max_workers
        )

    def _next_sequence(self) -> int:
        """Get next sequence number for FIFO ordering."""
        with self._sequence_lock:
            seq = self._sequence
            self._sequence += 1
            return seq

    def enqueue(self, task: Task) -> bool:
        """Add task to queue if not already seen.

        Args:
            task: Task to enqueue.

        Returns:
            bool: True if task was added, False if duplicate.
        """
        with self._seen_lock:
            if task.task_id in self._seen_tasks:
                logger.debug("Task %s already seen, skipping", task.task_id)
                return False
            self._seen_tasks.add(task.task_id)

        # Get priority value and sequence for ordering
        priority = self._PRIORITY_MAP.get(task.priority, 1)
        sequence = self._next_sequence()

        # Enqueue and notify waiting threads
        with self._task_available:
            self._queue.put(
                PrioritizedTask(priority=priority, sequence=sequence, task=task)
            )
            self._task_available.notify()

        logger.debug(
            "Enqueued task %s (priority=%s, seq=%d)",
            task.task_id,
            task.priority.name,
            sequence,
        )
        return True

    def queued_task_count(self) -> int:
        """Return the number of queued tasks (best-effort)."""
        return self._queue.qsize()

    def enqueue_many(self, tasks: list[Task]) -> int:
        """Enqueue multiple tasks.

        Args:
            tasks: Tasks to enqueue.

        Returns:
            int: Number of tasks actually enqueued.
        """
        count = 0
        for task in tasks:
            if self.enqueue(task):
                count += 1
        return count

    def _execute_task(self, task: Task) -> TaskResult:
        """Execute a single task with retry support.

        Args:
            task: Task to execute.

        Returns:
            TaskResult: Execution result.
        """
        config = task.retry_config or DEFAULT_RETRY_CONFIG
        start_time = time.time()
        last_error: Optional[Exception] = None
        attempt = 0

        logger.info("[PID %d] Executing task: %s", os.getpid(), task.task_id)

        while attempt <= config.max_retries:
            try:
                result = task.func()
                execution_time = time.time() - start_time
                if attempt > 0:
                    logger.info(
                        "[PID %d] Task %s succeeded on attempt %d in %.2fs",
                        os.getpid(),
                        task.task_id,
                        attempt + 1,
                        execution_time,
                    )
                else:
                    logger.info(
                        "[PID %d] Task %s completed in %.2fs",
                        os.getpid(),
                        task.task_id,
                        execution_time,
                    )
                return TaskResult(
                    task_id=task.task_id,
                    success=True,
                    result=result,
                    execution_time=execution_time,
                )

            except config.retryable_exceptions as e:
                last_error = e
                attempt += 1

                if attempt <= config.max_retries:
                    # Calculate delay with exponential backoff
                    delay = min(
                        config.base_delay * (config.exponential_base ** (attempt - 1)),
                        config.max_delay,
                    )
                    logger.warning(
                        "[PID %d] Task %s failed (attempt %d/%d), retrying in %.1fs: %s",
                        os.getpid(),
                        task.task_id,
                        attempt,
                        config.max_retries + 1,
                        delay,
                        e,
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        "[PID %d] Task %s failed after %d attempts: %s",
                        os.getpid(),
                        task.task_id,
                        attempt,
                        e,
                    )

            except Exception as e:
                # Non-retryable exception - fail immediately
                execution_time = time.time() - start_time
                logger.error(
                    "[PID %d] Task %s failed with non-retryable error: %s",
                    os.getpid(),
                    task.task_id,
                    e,
                    exc_info=True,  # Include full traceback for debugging
                )
                return TaskResult(
                    task_id=task.task_id,
                    success=False,
                    error=e,
                    execution_time=execution_time,
                )

        # All retries exhausted
        execution_time = time.time() - start_time
        return TaskResult(
            task_id=task.task_id,
            success=False,
            error=last_error,
            execution_time=execution_time,
        )

    def run_all(self) -> Dict[str, TaskResult]:
        """Execute all queued tasks with priority-based scheduling.

        Returns:
            Dict[str, TaskResult]: Results for all executed tasks.
        """
        logger.info(
            "[PID %d] Starting task execution with %d tasks",
            os.getpid(),
            self._queue.qsize(),
        )

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            self._executor = executor
            futures: Dict[Future, str] = {}
            last_activity = time.time()

            while True:
                # Submit new tasks up to max_workers limit
                submitted_count = 0
                while len(futures) < self.max_workers:
                    try:
                        prioritized_task = self._queue.get_nowait()
                        task = prioritized_task.task
                        future = executor.submit(self._execute_task, task)
                        futures[future] = task.task_id
                        submitted_count += 1
                        logger.debug(
                            "[PID %d] Submitted task %s", os.getpid(), task.task_id
                        )
                    except Empty:
                        break
                
                if submitted_count > 0:
                    last_activity = time.time()

                # If we have running tasks, wait for at least one to complete
                if futures:
                    done, _ = wait(futures.keys(), return_when=FIRST_COMPLETED)
                    for future in done:
                        task_id = futures.pop(future)
                        try:
                            result = future.result()
                            with self._results_lock:
                                self._results[task_id] = result
                            last_activity = time.time()
                        except Exception as e:
                            logger.error("Task %s failed unexpectedly: %s", task_id, e)
                            with self._results_lock:
                                self._results[task_id] = TaskResult(
                                    task_id=task_id,
                                    success=False,
                                    error=e,
                                    execution_time=0.0,
                                )
                            last_activity = time.time()
                    continue

                # If no tasks running but queue is empty
                # We need to wait for new tasks or timeout; loop until both queue
                # and futures remain empty after a timeout to avoid racing producers.
                with self._task_available:
                    if self._queue.empty():
                        notified = self._task_available.wait(
                            timeout=self._idle_wait_seconds
                        )
                        # If we were notified, loop and pick up tasks.
                        if notified:
                            continue
                        # Timed out: double-check queue without exiting early.
                        if self._queue.empty():
                            # No new tasks arrived during the wait; break out.
                            break

            self._executor = None

        successful = sum(1 for r in self._results.values() if r.success)
        failed = len(self._results) - successful
        logger.info(
            "[PID %d] Task execution completed: %d successful, %d failed",
            os.getpid(),
            successful,
            failed,
        )

        return self._results

    def get_results(self) -> Dict[str, TaskResult]:
        """Get results for all executed tasks.

        Returns:
            Dict[str, TaskResult]: Task results.
        """
        with self._results_lock:
            return dict(self._results)

    def clear(self) -> None:
        """Clear queue and results."""
        # PriorityQueue doesn't have clear(), create a new empty queue
        self._queue = PriorityQueue()
        with self._seen_lock:
            self._seen_tasks.clear()
        with self._results_lock:
            self._results.clear()
        with self._sequence_lock:
            self._sequence = 0
        logger.debug("Worker state cleared")
