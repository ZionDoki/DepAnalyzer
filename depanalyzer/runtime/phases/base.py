"""
Base phase class implementing template method pattern.

This module provides the BasePhase abstract class that defines the
execution framework for all lifecycle phases. It eliminates code
duplication by providing common logic for:
- Progress tracking
- Lifecycle hooks execution
- Exception handling
- Logging
- Error recovery (via IS_CRITICAL flag)

Subclasses only need to implement the execute() method with their
specific business logic.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

from depanalyzer.runtime.context import TransactionContext
from depanalyzer.runtime.lifecycle import LifecyclePhase

if TYPE_CHECKING:  # pragma: no cover
    from depanalyzer.runtime.transaction_state import TransactionState

# Safe exceptions that should be caught and handled gracefully
_SAFE_EXCEPTIONS = (
    RuntimeError,
    ValueError,
    TypeError,
    AttributeError,
    KeyError,
    IndexError,
    OSError,
    ImportError,
    LookupError,
)

logger = logging.getLogger("depanalyzer.transaction.phase")


class BasePhase(ABC):
    """
    Abstract base class for all lifecycle phases.

    This class implements the Template Method pattern, defining a fixed
    execution flow while allowing subclasses to customize specific steps.

    Execution Flow:
        1. _before_execute() - Phase setup (logging, state update)
        2. _run_hooks_before() - Execute before-phase hooks
        3. _start_progress() - Begin progress tracking
        4. execute() - **SUBCLASS IMPLEMENTS THIS** (core business logic)
        5. _complete_progress() - Complete progress tracking
        6. _run_hooks_after() - Execute after-phase hooks
        7. _after_execute() - Phase cleanup

    Error Handling:
        - If IS_CRITICAL=True: Exceptions propagate and halt the transaction
        - If IS_CRITICAL=False: Exceptions are logged as warnings and execution continues

    Example:
        class MyPhase(BasePhase):
            IS_CRITICAL = True

            def execute(self, context: TransactionContext) -> None:
                # Implement phase-specific logic here
                self.state.update_phase_result(
                    LifecyclePhase.PARSE,
                    'my_result',
                    some_value
                )

    Attributes:
        IS_CRITICAL: If True, phase failures halt the transaction.
                     If False, failures are logged but execution continues.
                     Default: True
        state: TransactionState instance shared across all phases
    """

    # Class attribute: determines error recovery behavior
    IS_CRITICAL: bool = True

    def __init__(self, state: "TransactionState") -> None:
        """
        Initialize the phase with shared state.

        Args:
            state: TransactionState instance containing all transaction state
        """
        self.state = state

    def run(self, context: TransactionContext) -> None:
        """
        Template method: execute the phase with standardized flow.

        This method coordinates the entire phase execution, including
        hooks, progress tracking, and error handling. Subclasses should
        NOT override this method - implement execute() instead.

        Args:
            context: Phase-specific context containing input data

        Raises:
            Exception: If IS_CRITICAL=True and execute() raises an exception
        """
        phase_name = self.__class__.__name__

        failed = False

        # 1. Phase setup
        self._before_execute(context)

        # 2. Execute before-phase hooks
        self._run_hooks_before(context)

        # 3. Start progress tracking
        phase_id = self._start_progress()

        try:
            # 4. Execute core logic (implemented by subclass)
            self.execute(context)

        except _SAFE_EXCEPTIONS as error:
            self._handle_error(error, phase_id)
            failed = True

            # Decide whether to propagate based on IS_CRITICAL
            if self.IS_CRITICAL:
                logger.error(
                    "Critical phase %s failed: %s",
                    phase_name,
                    error,
                    exc_info=True,
                )
                raise  # Propagate exception, halt transaction
            else:
                logger.warning(
                    "Non-critical phase %s failed: %s. Continuing execution...",
                    phase_name,
                    error,
                    exc_info=True,
                )
                # Don't propagate - allow transaction to continue

        finally:
            # 5. Complete progress tracking only when the phase did not fail
            if not failed:
                self._complete_progress(phase_id)

        # 6. Execute after-phase hooks
        self._run_hooks_after(context)

        # 7. Phase cleanup
        self._after_execute(context)

    @abstractmethod
    def execute(self, context: TransactionContext) -> None:
        """
        Execute the phase-specific business logic.

        Subclasses MUST implement this method with their phase logic.
        This method should:
        - Read inputs from the context
        - Perform phase-specific work
        - Update state via self.state.update_phase_result()

        Args:
            context: Phase-specific context with input data

        Example:
            def execute(self, context: ParseContext) -> None:
                # Read inputs from context
                targets = context.detected_targets

                # Do phase work
                results = self._parse_targets(targets)

                # Update state
                self.state.update_phase_result(
                    LifecyclePhase.PARSE,
                    'parse_results',
                    results
                )
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement execute() for {context.current_phase}"
        )

    # ========== Template Method Helper Methods ==========
    # These methods provide common logic used by all phases

    def _before_execute(self, _context: TransactionContext) -> None:
        """
        Phase setup: logging and state updates.

        Args:
            _context: Phase context (reserved for subclass use)
        """
        phase_enum = self._get_phase_enum()
        logger.info("=== Phase: %s ===", phase_enum.name)
        self.state.current_phase = phase_enum

    def _run_hooks_before(self, context: TransactionContext) -> None:
        """
        Execute before-phase lifecycle hooks.

        Args:
            context: Phase context passed to hooks
        """
        if not self.state.lifecycle_hooks:
            return

        current_phase = self.state.current_phase
        for hook in self.state.lifecycle_hooks:
            if hook.phase == current_phase:
                try:
                    hook.before(context)
                except _SAFE_EXCEPTIONS:  # pragma: no cover
                    logger.exception("Lifecycle hook %r before() failed", hook)

    def _start_progress(self) -> Optional[str]:
        """
        Start progress tracking for this phase.

        Returns:
            Package key for progress tracking, or None if no display manager
        """
        if not self.state.display_manager:
            return None

        phase_enum = self._get_phase_enum()

        # Get package key from source path
        pkg_key = self._get_package_key()

        # Register package if not already registered (for all transactions including main)
        if pkg_key:
            if not self.state.display_manager.package_tracker.get_package(pkg_key):
                self.state.display_manager.register_package(
                    name=pkg_key,
                    version="",
                    depth=self.state.current_depth,
                )

            # Start package phase tracking
            self.state.display_manager.start_package_phase(pkg_key, phase_enum)

        return pkg_key

    def _get_package_key(self) -> Optional[str]:
        """Get package key from transaction state."""
        # Try to get name from graph_metadata first
        if self.state.graph_metadata:
            name = self.state.graph_metadata.get("name")
            version = self.state.graph_metadata.get("version", "")
            if name:
                return f"{name}@{version}" if version else name

        # Fallback to source path basename
        if self.state.source:
            from pathlib import Path
            return Path(self.state.source).name

        return None

    def _complete_progress(self, pkg_key: Optional[str]) -> None:
        """
        Mark phase progress as complete.

        Args:
            pkg_key: Package key returned by _start_progress()
        """
        if self.state.display_manager and pkg_key:
            phase_enum = self._get_phase_enum()
            self.state.display_manager.complete_package_phase(pkg_key, phase_enum)

    def _run_hooks_after(self, context: TransactionContext) -> None:
        """
        Execute after-phase lifecycle hooks.

        Args:
            context: Phase context passed to hooks
        """
        if not self.state.lifecycle_hooks:
            return

        current_phase = self.state.current_phase
        for hook in self.state.lifecycle_hooks:
            if hook.phase == current_phase:
                try:
                    hook.after(context)
                except _SAFE_EXCEPTIONS:  # pragma: no cover
                    logger.exception("Lifecycle hook %r after() failed", hook)

    def _after_execute(self, _context: TransactionContext) -> None:
        """
        Phase cleanup (override if needed).

        Args:
            _context: Phase context (reserved for subclass use)
        """

    def _handle_error(self, error: Exception, pkg_key: Optional[str]) -> None:
        """
        Handle phase execution errors.

        Args:
            error: The exception that was raised
            pkg_key: Package key for progress tracking
        """
        if self.state.display_manager and pkg_key:
            self.state.display_manager.fail_package(pkg_key, str(error))

    def _get_phase_enum(self) -> LifecyclePhase:
        """
        Infer the LifecyclePhase enum from the class name.

        Returns:
            LifecyclePhase enum corresponding to this phase

        Raises:
            ValueError: If phase name cannot be mapped to an enum

        Example:
            AcquirePhase -> LifecyclePhase.ACQUIRE
            ParsePhase -> LifecyclePhase.PARSE
            ResolveDepsPhase -> LifecyclePhase.RESOLVE_DEPS
        """
        class_name = self.__class__.__name__

        # Remove "Phase" suffix
        if class_name.endswith("Phase"):
            phase_name = class_name[:-5]  # Remove "Phase"
        else:
            phase_name = class_name

        # Convert to uppercase
        phase_name = phase_name.upper()

        # Handle special cases
        if phase_name == "RESOLVEDEPS":
            phase_name = "RESOLVE_DEPS"

        try:
            return LifecyclePhase[phase_name]
        except KeyError as exc:
            raise ValueError(
                f"Cannot map class {self.__class__.__name__} to LifecyclePhase"
            ) from exc
