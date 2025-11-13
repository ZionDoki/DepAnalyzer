"""Concurrent task executor with queue-based scheduling.

Worker provides unified task queue management with deduplication,
rate limiting, and failure handling. All phases use the queue for
execution, avoiding recursion.
"""

import logging
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, Optional, Set

logger = logging.getLogger("depanalyzer.worker")


class TaskPriority(Enum):
    """Task priority levels for scheduling."""

    HIGH = auto()
    NORMAL = auto()
    LOW = auto()


@dataclass
class Task:
    """Task representation for worker queue.

    Attributes:
        task_id: Unique task identifier.
        func: Callable to execute.
        priority: Task priority level.
        metadata: Optional task metadata.
    """

    task_id: str
    func: Callable[[], Any]
    priority: TaskPriority = TaskPriority.NORMAL
    metadata: Dict[str, Any] = field(default_factory=dict)

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


class Worker:
    """Concurrent task executor with BFS queue-based scheduling.

    Worker manages a task queue with fingerprint-based deduplication,
    priority-based scheduling, and configurable parallelism.
    """

    def __init__(self, max_workers: int = 8) -> None:
        """Initialize worker with thread pool.

        Args:
            max_workers: Maximum concurrent threads.
        """
        self.max_workers = max_workers
        self._queue: deque[Task] = deque()
        self._seen_tasks: Set[str] = set()
        self._results: Dict[str, TaskResult] = {}
        self._lock = threading.RLock()
        self._executor: Optional[ThreadPoolExecutor] = None

        logger.info("Worker initialized with %d max workers", max_workers)

    def enqueue(self, task: Task) -> bool:
        """Add task to queue if not already seen.

        Args:
            task: Task to enqueue.

        Returns:
            bool: True if task was added, False if duplicate.
        """
        with self._lock:
            if task.task_id in self._seen_tasks:
                logger.debug("Task %s already seen, skipping", task.task_id)
                return False

            self._seen_tasks.add(task.task_id)
            # Insert based on priority
            if task.priority == TaskPriority.HIGH:
                self._queue.appendleft(task)
            else:
                self._queue.append(task)

            logger.debug("Enqueued task %s (priority=%s)", task.task_id, task.priority.name)
            return True

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
        """Execute a single task and return result.

        Args:
            task: Task to execute.

        Returns:
            TaskResult: Execution result.
        """
        start_time = time.time()
        logger.info("Executing task: %s", task.task_id)

        try:
            result = task.func()
            execution_time = time.time() - start_time
            logger.info("Task %s completed in %.2fs", task.task_id, execution_time)
            return TaskResult(
                task_id=task.task_id,
                success=True,
                result=result,
                execution_time=execution_time,
            )
        except Exception as e:
            execution_time = time.time() - start_time
            logger.error("Task %s failed: %s", task.task_id, e)
            return TaskResult(
                task_id=task.task_id,
                success=False,
                error=e,
                execution_time=execution_time,
            )

    def run_all(self) -> Dict[str, TaskResult]:
        """Execute all queued tasks with BFS scheduling.

        Returns:
            Dict[str, TaskResult]: Results for all executed tasks.
        """
        logger.info("Starting task execution with %d tasks", len(self._queue))

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            self._executor = executor
            futures: Dict[Future, str] = {}

            while self._queue or futures:
                # Submit new tasks up to max_workers limit
                while self._queue and len(futures) < self.max_workers:
                    with self._lock:
                        if not self._queue:
                            break
                        task = self._queue.popleft()

                    future = executor.submit(self._execute_task, task)
                    futures[future] = task.task_id
                    logger.debug("Submitted task %s for execution", task.task_id)

                # Wait for at least one task to complete
                if futures:
                    try:
                        completed = []
                        for future in as_completed(futures.keys(), timeout=5.0):
                            completed.append(future)

                        for future in completed:
                            task_id = futures.pop(future)
                            result = future.result()
                            with self._lock:
                                self._results[task_id] = result
                    except TimeoutError:
                        # Check for completed futures manually
                        completed = [f for f in futures.keys() if f.done()]
                        for future in completed:
                            task_id = futures.pop(future)
                            try:
                                result = future.result()
                                with self._lock:
                                    self._results[task_id] = result
                            except Exception as e:
                                logger.error("Task %s failed: %s", task_id, e)
                                with self._lock:
                                    self._results[task_id] = TaskResult(
                                        task_id=task_id,
                                        success=False,
                                        error=e,
                                        execution_time=0.0,
                                    )
                else:
                    time.sleep(0.01)

            self._executor = None

        successful = sum(1 for r in self._results.values() if r.success)
        failed = len(self._results) - successful
        logger.info("Task execution completed: %d successful, %d failed", successful, failed)

        return self._results

    def get_results(self) -> Dict[str, TaskResult]:
        """Get results for all executed tasks.

        Returns:
            Dict[str, TaskResult]: Task results.
        """
        with self._lock:
            return dict(self._results)

    def clear(self) -> None:
        """Clear queue and results."""
        with self._lock:
            self._queue.clear()
            self._seen_tasks.clear()
            self._results.clear()
            logger.debug("Worker state cleared")
