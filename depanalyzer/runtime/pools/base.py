"""Base classes for task pools.

This module provides the abstract base class for all task pools and the
ResultIterator for real-time result streaming.

Key features:
- Event-driven result notification
- ResultIterator for for-loop style result processing
- seal() method to indicate no more tasks will be added
- Zero-overhead design: lazy initialization and auto-shutdown
"""

from __future__ import annotations

import logging
import queue
import threading
from abc import ABC, abstractmethod
from typing import Generic, Iterator, Optional, TypeVar

logger = logging.getLogger("depanalyzer.runtime.pools.base")

T = TypeVar("T")  # Task type
R = TypeVar("R")  # Result type


class ResultIterator(Generic[R]):
    """Iterator for real-time result streaming via for-loop.

    This iterator yields results as they become available from the pool.
    It uses event-driven waiting to efficiently block until results are ready.

    Usage:
        pool = CodeParserPool(max_workers=4)
        pool.start()

        for file_path in files:
            pool.submit_file(file_path, ecosystem)
        pool.seal()

        for result in pool.results():
            process_result(result)
    """

    def __init__(self, pool: "BaseTaskPool") -> None:
        """Initialize the result iterator.

        Args:
            pool: The task pool to iterate over.
        """
        self._pool = pool

    def __iter__(self) -> Iterator[R]:
        """Return self as iterator."""
        return self

    def __next__(self) -> R:
        """Get next result from pool.

        Uses event-driven approach to wait for results. Will block until
        a result is available. Only raises StopIteration when the pool
        is sealed AND all tasks are completed.

        Returns:
            Next result from the pool.

        Raises:
            StopIteration: When pool is sealed and all tasks are completed.
        """
        while True:
            # Try to get existing result first (non-blocking)
            result = self._pool.get_result(block=False)
            if result is not None:
                return result

            # Check for completion if pool is sealed
            if self._pool.is_sealed():
                if self._pool.is_complete():
                    # Final check for any remaining results
                    result = self._pool.get_result(block=False)
                    if result is not None:
                        return result
                    raise StopIteration

            # Wait for new result to be available (event-driven)
            if not self._pool.wait_for_result(timeout=0.5):
                # Timeout occurred, continue loop to check status again
                continue


class BaseTaskPool(ABC, Generic[T, R]):
    """Abstract base class for all task pools.

    This class provides the common infrastructure for task pools:
    - Event-driven result notification
    - ResultIterator for real-time streaming
    - seal() method to indicate no more tasks
    - Zero-overhead design with lazy initialization

    Subclasses must implement:
    - _create_executor(): Create the underlying executor
    - _worker_loop(): Main loop for background worker thread

    Attributes:
        max_workers: Maximum number of worker processes/threads.
        name: Name of the pool for logging.
    """

    def __init__(self, max_workers: int, name: str = "TaskPool") -> None:
        """Initialize the task pool.

        Args:
            max_workers: Maximum number of worker processes/threads.
            name: Name of the pool for logging.
        """
        self.max_workers = max_workers
        self.name = name

        # Task and result queues
        self._task_queue: queue.Queue[Optional[T]] = queue.Queue()
        self._result_queue: queue.Queue[R] = queue.Queue()

        # Event-driven synchronization
        self._result_available_event = threading.Event()
        self._completion_event = threading.Event()
        self._shutdown_event = threading.Event()

        # State tracking
        self._sealed = False
        self._seal_lock = threading.Lock()
        self._pending_count = 0
        self._pending_lock = threading.Lock()
        self._started = False
        self._start_lock = threading.Lock()

        # Worker thread
        self._worker_thread: Optional[threading.Thread] = None

        logger.debug("%s initialized with max_workers=%d", self.name, self.max_workers)

    @abstractmethod
    def _create_executor(self):
        """Create the underlying executor (Process or Thread pool).

        Returns:
            The executor instance.
        """
        pass

    @abstractmethod
    def _worker_loop(self) -> None:
        """Main loop for background worker thread.

        This method should:
        1. Create the executor
        2. Submit tasks from task_queue to executor
        3. Collect results and put them in result_queue
        4. Set _result_available_event when results are ready
        5. Set _completion_event when all tasks are done
        6. Clean up executor on shutdown
        """
        pass

    def start(self) -> None:
        """Start the background worker thread.

        This method is idempotent - calling it multiple times has no effect
        if the pool is already started.
        """
        with self._start_lock:
            if self._started:
                return

            self._started = True
            self._shutdown_event.clear()
            self._completion_event.clear()
            self._sealed = False

            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                name=f"{self.name}Worker",
                daemon=False,
            )
            self._worker_thread.start()
            logger.debug("%s started", self.name)

    def submit(self, task: T) -> None:
        """Submit a task for execution.

        Args:
            task: The task to execute.

        Raises:
            RuntimeError: If the pool is sealed.
        """
        with self._seal_lock:
            if self._sealed:
                raise RuntimeError(f"{self.name} is sealed, cannot submit new tasks")

        # Auto-start if not started
        if not self._started:
            self.start()

        with self._pending_lock:
            self._pending_count += 1

        self._task_queue.put(task)
        self._completion_event.clear()  # New task means not complete

    def seal(self) -> None:
        """Indicate that no more tasks will be submitted.

        After calling seal(), the ResultIterator will stop when all
        submitted tasks are complete. Calling submit() after seal()
        will raise RuntimeError.
        """
        with self._seal_lock:
            if self._sealed:
                return
            self._sealed = True

        # Put sentinel to signal worker thread
        self._task_queue.put(None)
        logger.debug("%s sealed", self.name)

    def is_sealed(self) -> bool:
        """Check if the pool is sealed.

        Returns:
            True if seal() has been called.
        """
        with self._seal_lock:
            return self._sealed

    def is_complete(self) -> bool:
        """Check if all tasks are complete.

        Returns:
            True if pool is sealed and all tasks have finished.
        """
        return self._completion_event.is_set()

    def get_result(self, block: bool = True, timeout: Optional[float] = None) -> Optional[R]:
        """Get next result from result queue.

        Args:
            block: Whether to block waiting for result.
            timeout: Maximum time to wait if blocking.

        Returns:
            Next result, or None if no result available (non-blocking).
        """
        try:
            if block:
                return self._result_queue.get(timeout=timeout)
            else:
                return self._result_queue.get_nowait()
        except queue.Empty:
            return None

    def wait_for_result(self, timeout: Optional[float] = None) -> bool:
        """Wait for a result to be available using event-driven approach.

        Args:
            timeout: Maximum time to wait for result.

        Returns:
            True if result is available, False if timeout occurred.
        """
        # Check if results are already available
        if not self._result_queue.empty():
            return True

        # Clear event and wait
        self._result_available_event.clear()
        return self._result_available_event.wait(timeout=timeout)

    def results(self) -> ResultIterator[R]:
        """Get an iterator for real-time result processing.

        Returns an event-driven iterator that yields results as they
        become available. Perfect for use in for-loops.

        Returns:
            ResultIterator for processing results in real-time.

        Example:
            for result in pool.results():
                print(f"Processed: {result.get('file')}")
        """
        return ResultIterator(self)

    def get_pending_count(self) -> int:
        """Get number of pending tasks in queue.

        Returns:
            Number of tasks waiting to be processed.
        """
        return self._task_queue.qsize()

    def get_active_count(self) -> int:
        """Get number of tasks currently being processed.

        Returns:
            Number of active tasks (submitted but not yet completed).
        """
        with self._pending_lock:
            return self._pending_count

    def shutdown(self, wait: bool = True, timeout: float = 30.0) -> None:
        """Shutdown the pool and release resources.

        Args:
            wait: Whether to wait for worker thread to finish.
            timeout: Maximum time to wait for shutdown.
        """
        logger.debug("%s shutting down (wait=%s)", self.name, wait)

        self._shutdown_event.set()

        # Seal if not already sealed
        if not self.is_sealed():
            self.seal()

        if wait and self._worker_thread is not None:
            self._worker_thread.join(timeout=timeout)
            if self._worker_thread.is_alive():
                logger.warning("%s worker thread did not terminate within timeout", self.name)

        self._started = False
        logger.debug("%s shut down", self.name)

    def _decrement_pending(self) -> int:
        """Decrement pending count and return new value.

        Returns:
            New pending count after decrement.
        """
        with self._pending_lock:
            self._pending_count -= 1
            return self._pending_count

    def _check_completion(self) -> None:
        """Check if all tasks are complete and set completion event."""
        with self._seal_lock:
            sealed = self._sealed
        with self._pending_lock:
            pending = self._pending_count

        if sealed and pending == 0:
            self._completion_event.set()
            logger.debug("%s all tasks complete", self.name)

    def __enter__(self) -> "BaseTaskPool":
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        if not self.is_sealed():
            self.seal()
        self.shutdown(wait=True)
