"""Dependency collector for event-driven dependency discovery.

This component subscribes to ``DEPENDENCY_DISCOVERED`` events on the
runtime event bus and accumulates ``DependencySpec`` instances for
later resolution during the RESOLVE_DEPS phase.
"""

# Event handlers intentionally catch all exceptions to avoid disrupting transactions.


from __future__ import annotations

import logging
import threading
from typing import List

from depanalyzer.runtime.eventbus import Event, EventType, EventBus
from depanalyzer.parsers.base import DependencySpec

logger = logging.getLogger("depanalyzer.runtime.dependency_collector")


class DependencyCollector:
    """Collect dependency specifications from parser events.

    Parsers emit ``DEPENDENCY_DISCOVERED`` events when they encounter
    third-party dependencies. The collector subscribes to these events
    and stores the corresponding ``DependencySpec`` objects so that the
    transaction can resolve them in a later phase.

    Thread-safe: uses a lock to protect the dependency list.
    """

    def __init__(self, eventbus: EventBus) -> None:
        """Initialize the dependency collector.

        Args:
            eventbus: Event bus for subscribing to events.
        """
        self.eventbus = eventbus
        self._discovered_deps: List[DependencySpec] = []
        self._lock = threading.Lock()  # Thread-safe access to _discovered_deps
        self._register_handlers()

    def _register_handlers(self) -> None:
        """Register event handlers with the event bus."""
        self.eventbus.subscribe(
            EventType.DEPENDENCY_DISCOVERED,
            self._handle_dependency_discovered,
            name="dependency_collector.dependency_discovered",
        )
        logger.info("DependencyCollector registered event handlers")

    def _handle_dependency_discovered(self, event: Event) -> None:
        """Handle DEPENDENCY_DISCOVERED event by collecting the dependency spec.

        Args:
            event: Event containing dependency specification.
        """
        try:
            data = event.data
            spec = data.get("spec")

            if not spec:
                logger.warning(
                    "DEPENDENCY_DISCOVERED event missing 'spec' field from %s",
                    event.source,
                )
                return

            if not isinstance(spec, DependencySpec):
                logger.warning(
                    "Expected DependencySpec instance, got %s from %s",
                    type(spec).__name__,
                    event.source,
                )
                return

            with self._lock:
                self._discovered_deps.append(spec)
            logger.debug(
                "Collected dependency: %s/%s (ecosystem=%s) from %s",
                spec.name,
                spec.version or "latest",
                spec.ecosystem,
                event.source,
            )
        except (KeyError, AttributeError, TypeError) as exc:
            logger.error(
                "Failed to handle DEPENDENCY_DISCOVERED event from %s: %s",
                event.source,
                exc,
                exc_info=True,
            )

    def get_discovered_dependencies(self) -> List[DependencySpec]:
        """Return all discovered dependencies.

        Thread-safe: returns a copy of the list.

        Returns:
            List[DependencySpec]: Collected dependency specifications.
        """
        with self._lock:
            return self._discovered_deps.copy()

    def clear(self) -> None:
        """Clear all collected dependency specifications.

        Thread-safe.
        """
        with self._lock:
            self._discovered_deps.clear()
        logger.debug("Cleared all collected dependencies")


__all__ = ["DependencyCollector"]
