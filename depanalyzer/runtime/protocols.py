"""
Protocol definitions for runtime components.

Protocols provide abstract interfaces for dependency injection,
avoiding circular imports and improving testability.
"""

from typing import Protocol, Any, Optional


class TransactionFactory(Protocol):
    """
    Factory protocol for creating Transaction instances.

    This protocol is used to avoid circular imports when ResolveDepsPhase
    needs to create child Transaction instances. Instead of importing the
    Transaction class directly, phases receive a factory that implements
    this protocol.

    Example:
        # In ResolveDepsPhase
        child_tx = factory.create(
            source="sub_module",
            max_dependency_depth=1,
        )
        result = child_tx.run()
    """

    def create(
        self,
        source: str,
        graph_id: Optional[str] = None,
        **kwargs: Any
    ) -> Any:
        """
        Create a new Transaction instance.

        Args:
            source: Source to analyze (path, URL, etc.)
            graph_id: Optional graph identifier
            **kwargs: Additional configuration parameters

        Returns:
            A Transaction instance (typed as Any to avoid circular imports)
        """
        ...
