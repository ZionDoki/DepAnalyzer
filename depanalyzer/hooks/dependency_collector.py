"""Dependency Collector Hook.

Subscribes to DEPENDENCY_DISCOVERED events and collects dependency specifications
for later resolution and fetching.
"""

from __future__ import annotations

import logging
from typing import List

from depanalyzer.runtime.eventbus import Event, EventType, EventBus
from depanalyzer.parsers.base import DependencySpec

logger = logging.getLogger("depanalyzer.hooks.dependency_collector")


class DependencyCollector:
    """Hook that collects dependency specifications from parser events.

    This hook subscribes to DEPENDENCY_DISCOVERED events and maintains a list
    of all third-party dependencies discovered during the parsing phase.
    """

    def __init__(self, eventbus: EventBus):
        """Initialize the dependency collector hook.

        Args:
            eventbus: Event bus for subscribing to events.
        """
        self.eventbus = eventbus
        self._discovered_deps: List[DependencySpec] = []
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

            # Ensure spec is a DependencySpec instance
            if not isinstance(spec, DependencySpec):
                logger.warning(
                    "Expected DependencySpec instance, got %s from %s",
                    type(spec).__name__,
                    event.source,
                )
                return

            # Add to discovered dependencies
            self._discovered_deps.append(spec)
            logger.debug(
                "Collected dependency: %s/%s (ecosystem=%s) from %s",
                spec.name,
                spec.version or "latest",
                spec.ecosystem,
                event.source,
            )

        except Exception as e:
            logger.error(
                "Failed to handle DEPENDENCY_DISCOVERED event from %s: %s",
                event.source,
                e,
                exc_info=True,
            )

    def get_discovered_dependencies(self) -> List[DependencySpec]:
        """Get all discovered dependencies.

        Returns:
            List[DependencySpec]: List of all discovered dependency specifications.
        """
        return self._discovered_deps.copy()

    def clear(self) -> None:
        """Clear all collected dependencies."""
        self._discovered_deps.clear()
        logger.debug("Cleared all collected dependencies")
