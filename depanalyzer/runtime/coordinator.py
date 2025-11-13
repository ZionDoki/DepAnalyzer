"""Global process pool coordinator for parallel transaction execution.

TransactionCoordinator manages a global ProcessPoolExecutor that executes
Transaction instances in parallel across multiple processes, avoiding Python's GIL.
"""

import logging
import os
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

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
    import pickle
    import time

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
        # Execute transaction
        graph_manager = transaction.run()

        execution_time = time.time() - start_time

        # Build result
        result = TransactionResult(
            transaction_id=transaction.transaction_id,
            graph_id=transaction.graph_id,
            success=True,
            node_count=graph_manager.node_count() if graph_manager else 0,
            edge_count=graph_manager.edge_count() if graph_manager else 0,
            execution_time=execution_time,
            parent_transaction_id=transaction.parent_transaction_id,
        )

        logger.info(
            "[PID %d] Transaction %s completed in %.2fs (%d nodes, %d edges)",
            process_id,
            transaction.transaction_id,
            execution_time,
            result.node_count,
            result.edge_count,
        )

        return result

    except Exception as e:
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
            "TransactionCoordinator initialized with %d max processes", self.max_processes
        )

        TransactionCoordinator._initialized = True

    @classmethod
    def get_instance(cls, max_processes: Optional[int] = None) -> "TransactionCoordinator":
        """Get the singleton instance.

        Args:
            max_processes: Maximum concurrent processes (only used on first call).

        Returns:
            TransactionCoordinator: Global instance.
        """
        if cls._instance is None:
            cls._instance = cls(max_processes)
        return cls._instance

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
        import pickle

        # Ensure executor is created
        if self._executor is None:
            self._executor = ProcessPoolExecutor(max_workers=self.max_processes)
            logger.info("Process pool executor started with %d workers", self.max_processes)

        # Serialize transaction
        try:
            pickle_data = pickle.dumps(transaction)
        except Exception as e:
            logger.error("Failed to serialize transaction %s: %s", transaction.transaction_id, e)
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
                        logger.info("Transaction %s completed successfully", transaction_id)
                    else:
                        logger.error("Transaction %s failed: %s", transaction_id, result.error)

                except Exception as e:
                    logger.error("Failed to get result for transaction %s: %s", transaction_id, e)
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

            logger.info("All transactions completed: %d successful, %d failed", successful, failed)

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

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the process pool.

        Args:
            wait: Whether to wait for pending tasks to complete.
        """
        if self._executor is not None:
            logger.info("Shutting down process pool (wait=%s)", wait)
            self._executor.shutdown(wait=wait)
            self._executor = None
            self._futures.clear()
            logger.info("Process pool shut down")

    def __enter__(self) -> "TransactionCoordinator":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.shutdown(wait=True)
