"""Task pool implementations for parallel processing.

This package provides specialized task pools for different task types:
- CodeParserPool: For CPU-intensive code parsing tasks
- DependencyFetchPool: For network I/O dependency fetching tasks

All pools follow a zero-overhead design:
- Lazy initialization: Executor created on first task submission
- Auto-shutdown: Executor released when all tasks complete
- Reusable: Can submit new tasks after completion
"""

from depanalyzer.runtime.pools.base import BaseTaskPool, ResultIterator
from depanalyzer.runtime.pools.code_parser_pool import CodeParserPool
from depanalyzer.runtime.pools.dep_fetch_pool import DependencyFetchPool

__all__ = [
    "BaseTaskPool",
    "ResultIterator",
    "CodeParserPool",
    "DependencyFetchPool",
]
