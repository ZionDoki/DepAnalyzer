"""Global process pool coordinator for parallel transaction execution.

TransactionCoordinator manages a global ProcessPoolExecutor that executes
Transaction instances in parallel across multiple processes, avoiding Python's GIL.
"""

# Coordinator protects against worker crashes to keep the scheduler alive.


import logging
import multiprocessing
import os
import pickle
import time
from concurrent.futures import (
    BrokenExecutor,
    CancelledError,
    Future,
    ProcessPoolExecutor,
    as_completed,
)
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from depanalyzer.runtime.transaction import Transaction

logger = logging.getLogger("depanalyzer.coordinator")


@dataclass
class TransactionResult:
    """Result of a transaction execution.

    Attributes:
        transaction_id: Unique transaction identifier.
        graph_id: Graph identifier for this transaction.
        success: Whether execution succeeded.
        node_count: Number of nodes in the graph.
        edge_count: Number of edges in the graph.
        execution_time: Time taken in seconds.
        error: Error message if failed.
        parent_transaction_id: Parent transaction ID if this is a child.
    """

    transaction_id: str
    graph_id: str
    success: bool
    node_count: int
    edge_count: int
    execution_time: float
    error: Optional[str] = None
    parent_transaction_id: Optional[str] = None


def _run_transaction_worker(transaction_pickle_data: bytes) -> TransactionResult:
    """Worker function to run a transaction in a separate process.

    This function is designed to be picklable and executable in a subprocess.

    Args:
        transaction_pickle_data: Pickled transaction data.

    Returns:
        TransactionResult: Execution result.
    """
    # Reconstruct transaction from pickle data
    transaction = pickle.loads(transaction_pickle_data)

    process_id = os.getpid()
    logger.info(
        "[PID %d] Starting transaction: %s (source: %s)",
        process_id,
        transaction.transaction_id,
        transaction.source,
    )

    start_time = time.time()

    try:
        # Execute transaction - run() returns TransactionResult
        result = transaction.run()

        execution_time = time.time() - start_time

        logger.info(
            "[PID %d] Transaction %s completed in %.2fs (%d nodes, %d edges)",
            process_id,
            transaction.transaction_id,
            execution_time,
            result.node_count,
            result.edge_count,
        )

        return result

    except (
        OSError,
        ValueError,
        RuntimeError,
        TypeError,
        AttributeError,
        ImportError,
        KeyError,
    ) as e:
        execution_time = time.time() - start_time
        logger.error(
            "[PID %d] Transaction %s failed after %.2fs: %s",
            process_id,
            transaction.transaction_id,
            execution_time,
            e,
            exc_info=True,
        )

        return TransactionResult(
            transaction_id=transaction.transaction_id,
            graph_id=transaction.graph_id or "unknown",
            success=False,
            node_count=0,
            edge_count=0,
            execution_time=execution_time,
            error=str(e),
            parent_transaction_id=transaction.parent_transaction_id,
        )


class TransactionCoordinator:
    """Global coordinator for parallel transaction execution.

    Manages a ProcessPoolExecutor that runs transactions in separate processes,
    providing full isolation and avoiding Python's GIL limitations.

    This class implements the Singleton pattern to ensure a single global instance.
    """

    _instance: Optional["TransactionCoordinator"] = None
    _initialized: bool = False

    def __new__(cls, *args, **kwargs):
        """Singleton pattern implementation."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, max_processes: Optional[int] = None) -> None:
        """Initialize coordinator with process pool.

        Args:
            max_processes: Maximum concurrent processes. Defaults to CPU count.
        """
        # Only initialize once
        if TransactionCoordinator._initialized:
            return

        self.max_processes = max_processes or os.cpu_count() or 4
        self._executor: Optional[ProcessPoolExecutor] = None
        self._futures: Dict[Future, str] = {}  # Future -> transaction_id
        self._results: Dict[str, TransactionResult] = {}  # transaction_id -> result
        self._pending_transactions: List[Tuple[str, bytes]] = []  # (tx_id, pickle_data)

        logger.info(
            "TransactionCoordinator initialized with %d max processes",
            self.max_processes,
        )

        TransactionCoordinator._initialized = True

    @classmethod
    def get_instance(
        cls, max_processes: Optional[int] = None
    ) -> "TransactionCoordinator":
        """Get the singleton instance.

        Args:
            max_processes: Maximum concurrent processes (only used on first call).

        Returns:
            TransactionCoordinator: Global instance.
        """
        if cls._instance is None:
            cls._instance = cls(max_processes)
            return cls._instance

        instance: "TransactionCoordinator" = cls._instance

        if (
            max_processes is not None
            and instance._executor is None
            and max_processes != instance.max_processes
        ):
            instance.max_processes = max_processes
            logger.info(
                "Reconfigured TransactionCoordinator max_processes to %d",
                instance.max_processes,
            )
        elif (
            max_processes is not None
            and instance._executor is not None
            and max_processes != instance.max_processes
        ):
            logger.debug(
                "TransactionCoordinator already started with %d workers, ignoring new value %d",
                instance.max_processes,
                max_processes,
            )

        return instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (mainly for testing)."""
        if cls._instance is not None:
            cls._instance.shutdown()
        cls._instance = None
        cls._initialized = False

    def submit(self, transaction: "Transaction") -> Future:
        """Submit a transaction for parallel execution.

        Args:
            transaction: Transaction instance to execute.

        Returns:
            Future: Future object for tracking execution.
        """
        # Ensure executor is created
        if self._executor is None:
            # Only force 'spawn' on Windows; on POSIX, prefer fork to dodge
            # Python 3.12 spawn shutdown hangs.
            executor_kwargs = {"max_workers": self.max_processes}
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

            self._executor = ProcessPoolExecutor(**executor_kwargs)
            logger.info(
                "Process pool executor started with %d workers (start_method=%s)",
                self.max_processes,
                start_method,
            )

        # Serialize transaction
        try:
            pickle_data = pickle.dumps(transaction)
        except Exception as e:
            logger.error(
                "Failed to serialize transaction %s: %s", transaction.transaction_id, e
            )
            raise

        # Submit to process pool
        future = self._executor.submit(_run_transaction_worker, pickle_data)
        self._futures[future] = transaction.transaction_id

        logger.info(
            "Submitted transaction %s to process pool (%d active futures)",
            transaction.transaction_id,
            len(self._futures),
        )

        return future

    def wait_all(self, timeout: Optional[float] = None) -> Dict[str, TransactionResult]:
        """Wait for all submitted transactions to complete.

        Args:
            timeout: Optional timeout in seconds.

        Returns:
            Dict[str, TransactionResult]: Results for all transactions.

        Raises:
            TimeoutError: If timeout is reached before completion.
        """
        if not self._futures:
            logger.info("No transactions to wait for")
            return self._results

        logger.info("Waiting for %d transactions to complete", len(self._futures))

        try:
            for future in as_completed(self._futures.keys(), timeout=timeout):
                transaction_id = self._futures[future]

                try:
                    result = future.result()
                    self._results[transaction_id] = result

                    if result.success:
                        logger.info(
                            "Transaction %s completed successfully", transaction_id
                        )
                    else:
                        logger.error(
                            "Transaction %s failed: %s", transaction_id, result.error
                        )

                except (
                    CancelledError,
                    BrokenExecutor,
                    pickle.PickleError,
                    OSError,
                    RuntimeError,
                ) as e:
                    logger.error(
                        "Failed to get result for transaction %s: %s", transaction_id, e
                    )
                    self._results[transaction_id] = TransactionResult(
                        transaction_id=transaction_id,
                        graph_id="unknown",
                        success=False,
                        node_count=0,
                        edge_count=0,
                        execution_time=0.0,
                        error=str(e),
                    )

            # Clear futures after completion
            self._futures.clear()

            successful = sum(1 for r in self._results.values() if r.success)
            failed = len(self._results) - successful

            logger.info(
                "All transactions completed: %d successful, %d failed",
                successful,
                failed,
            )

            return self._results

        except TimeoutError:
            logger.error("Timeout waiting for transactions to complete")
            raise

    def get_result(self, transaction_id: str) -> Optional[TransactionResult]:
        """Get result for a specific transaction.

        Args:
            transaction_id: Transaction identifier.

        Returns:
            Optional[TransactionResult]: Result if available, None otherwise.
        """
        return self._results.get(transaction_id)

    def get_all_results(self) -> Dict[str, TransactionResult]:
        """Get all collected results.

        Returns:
            Dict[str, TransactionResult]: All transaction results.
        """
        return dict(self._results)

    def _log_executor_diagnostics(self, when: str) -> None:
        """Log best-effort diagnostics for the process pool to debug shutdown hangs."""
        executor = self._executor
        if executor is None:
            logger.debug("Executor diagnostics (%s): executor is None", when)
            return

        # Future state summary
        try:
            pending = [tx_id for future, tx_id in self._futures.items() if not future.done()]
            done = [tx_id for future, tx_id in self._futures.items() if future.done()]
            logger.info(
                "Executor diagnostics (%s): futures total=%d, pending=%d (%s), done=%d (%s)",
                when,
                len(self._futures),
                len(pending),
                ", ".join(pending) or "-",
                len(done),
                ", ".join(done) or "-",
            )
        except Exception as exc:  # pragma: no cover - diagnostics only
            logger.debug(
                "Executor diagnostics (%s) failed while summarizing futures: %s",
                when,
                exc,
            )

        # Process state summary (uses private attributes defensively)
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
                    "Executor diagnostics (%s): processes=%d [%s]",
                    when,
                    len(processes),
                    ", ".join(statuses),
                )
            else:
                logger.info("Executor diagnostics (%s): processes=0", when)
        except Exception as exc:  # pragma: no cover - diagnostics only
            logger.debug(
                "Executor diagnostics (%s) failed while summarizing processes: %s",
                when,
                exc,
            )

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the process pool.

        Args:
            wait: Whether to wait for pending tasks to complete.
        """
        if self._executor is not None:
            self._log_executor_diagnostics("pre-shutdown")
            logger.info("Shutting down process pool (wait=%s)", wait)
            executor = self._executor
            self._executor = None

            # Use wait=False to avoid blocking on Python 3.12 spawn shutdown hangs.
            executor.shutdown(wait=False)
            if wait:
                self._wait_for_executor_processes(executor, timeout=10.0)

            self._executor = None
            self._futures.clear()
            logger.info("Process pool shut down")

    def __enter__(self) -> "TransactionCoordinator":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.shutdown(wait=True)

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
            "Force-terminating %d stuck worker process(es): %s",
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
                logger.debug("Failed to join terminated process %s", getattr(proc, "pid", "?"))
