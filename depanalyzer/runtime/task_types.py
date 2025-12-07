"""Task type definitions for the global task pool architecture.

This module defines the core data structures for the task-based execution model:
- TaskType: Enum of all supported task types
- Task: Immutable task specification
- TaskResult: Result container for completed tasks
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class TaskType(Enum):
    """Types of tasks that can be executed in the global task pool."""

    # Detection tasks
    DETECT_ECOSYSTEM = "detect_ecosystem"

    # Parsing tasks
    PARSE_CONFIG = "parse_config"
    PARSE_CODE = "parse_code"

    # Dependency tasks
    FETCH_DEPENDENCY = "fetch_dependency"

    # Analysis tasks
    ANALYZE_GRAPH = "analyze_graph"

    # Export tasks
    EXPORT_GRAPH = "export_graph"


@dataclass(frozen=True)
class Task:
    """Immutable task specification for execution in the global task pool.

    Tasks are the atomic unit of work in the new architecture. Each task:
    - Has a unique ID for tracking
    - Has a type that determines the executor function
    - Contains a JSON-serializable payload
    - Has an optional timeout
    - Can depend on other tasks via parent_task_id

    Attributes:
        task_id: Unique identifier for this task.
        task_type: Type of task (determines executor).
        payload: JSON-serializable data for the task.
        timeout: Maximum execution time in seconds.
        priority: Higher priority tasks are executed first.
        parent_task_id: ID of task this depends on (for scheduling).
        transaction_id: ID of the transaction this task belongs to.
    """

    task_type: TaskType
    payload: Dict[str, Any]
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timeout: float = 60.0
    priority: int = 0
    parent_task_id: Optional[str] = None
    transaction_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert task to JSON-serializable dictionary.

        Returns:
            Dict containing all task attributes.
        """
        return {
            "task_id": self.task_id,
            "task_type": self.task_type.value,
            "payload": self.payload,
            "timeout": self.timeout,
            "priority": self.priority,
            "parent_task_id": self.parent_task_id,
            "transaction_id": self.transaction_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        """Create Task from dictionary.

        Args:
            data: Dictionary with task attributes.

        Returns:
            Task instance.
        """
        return cls(
            task_id=data["task_id"],
            task_type=TaskType(data["task_type"]),
            payload=data["payload"],
            timeout=data.get("timeout", 60.0),
            priority=data.get("priority", 0),
            parent_task_id=data.get("parent_task_id"),
            transaction_id=data.get("transaction_id"),
        )


@dataclass
class TaskResult:
    """Result container for a completed task.

    Attributes:
        task_id: ID of the completed task.
        success: Whether the task completed successfully.
        result: Task output data (if successful).
        error: Error message (if failed).
        execution_time: Time taken to execute in seconds.
    """

    task_id: str
    success: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    execution_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to JSON-serializable dictionary."""
        return {
            "task_id": self.task_id,
            "success": self.success,
            "result": self.result,
            "error": self.error,
            "execution_time": self.execution_time,
        }


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


@dataclass
class TaskBatch:
    """A batch of related tasks for bulk submission.

    Attributes:
        batch_id: Unique identifier for this batch.
        tasks: List of tasks in this batch.
        transaction_id: Transaction these tasks belong to.
    """

    tasks: List[Task]
    batch_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    transaction_id: Optional[str] = None

    def __len__(self) -> int:
        return len(self.tasks)

    def __iter__(self):
        return iter(self.tasks)


# Task priority constants
PRIORITY_HIGH = 100
PRIORITY_NORMAL = 50
PRIORITY_LOW = 0

# Default timeouts by task type (seconds)
DEFAULT_TIMEOUTS: Dict[TaskType, float] = {
    TaskType.DETECT_ECOSYSTEM: 30.0,
    TaskType.PARSE_CONFIG: 60.0,
    TaskType.PARSE_CODE: 60.0,
    TaskType.FETCH_DEPENDENCY: 120.0,
    TaskType.ANALYZE_GRAPH: 300.0,
    TaskType.EXPORT_GRAPH: 60.0,
}


def create_task(
    task_type: TaskType,
    payload: Dict[str, Any],
    *,
    timeout: Optional[float] = None,
    priority: int = PRIORITY_NORMAL,
    parent_task_id: Optional[str] = None,
    transaction_id: Optional[str] = None,
) -> Task:
    """Factory function to create a task with sensible defaults.

    Args:
        task_type: Type of task to create.
        payload: Task payload data.
        timeout: Override default timeout (uses DEFAULT_TIMEOUTS if None).
        priority: Task priority (higher = sooner).
        parent_task_id: ID of parent task (for dependencies).
        transaction_id: ID of owning transaction.

    Returns:
        Configured Task instance.
    """
    effective_timeout = timeout if timeout is not None else DEFAULT_TIMEOUTS.get(task_type, 60.0)

    return Task(
        task_type=task_type,
        payload=payload,
        timeout=effective_timeout,
        priority=priority,
        parent_task_id=parent_task_id,
        transaction_id=transaction_id,
    )
