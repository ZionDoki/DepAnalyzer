"""Event bus for parser→hook communication.

Parsers publish structured events (e.g., target detected, module parsed),
and hooks subscribe to these events to perform cross-domain associations.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from threading import RLock
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("depanalyzer.eventbus")


class EventType(Enum):
    """Event types published by parsers and consumed by hooks.

    Events represent facts discovered during parsing that may require
    cross-domain association by hooks.
    """

    # Detection events
    TARGET_DETECTED = auto()
    MODULE_DETECTED = auto()

    # Parse completion events
    MODULE_PARSED = auto()
    TARGET_PARSED = auto()
    ARTIFACT_PRODUCED = auto()

    # Dependency events
    DEPENDENCY_DISCOVERED = auto()
    EXTERNAL_DEP_RESOLVED = auto()

    # Configuration/toolchain events
    TOOLCHAIN_DETECTED = auto()
    BUILD_CONFIG_DETECTED = auto()
    PACKAGING_BOUNDARY = auto()

    # Native/CMake specific
    CMAKE_TARGET_COMPLETED = auto()
    CMAKE_TARGET_CREATED = auto()
    CMAKE_SOURCE_FILES_ADDED = auto()
    CMAKE_LINK_DEPENDENCY_FOUND = auto()
    CMAKE_INCLUDE_DIRS_DECLARED = auto()
    CMAKE_EXTERNAL_PACKAGE_REFERENCED = auto()
    HVIGOR_NATIVE_DIR_FOUND = auto()


@dataclass
class Event:
    """Structured event payload.

    Events carry facts and location information discovered by parsers.
    Graph operations are performed by hooks in response to events.

    Attributes:
        event_type: Type of event.
        source: Source identifier (parser name, module path, etc.).
        data: Event payload containing facts and evidence.
    """

    event_type: EventType
    source: str
    data: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        """Return human-readable event description.

        Returns:
            str: Event description.
        """
        return f"Event({self.event_type.name}, source={self.source})"


EventHandler = Callable[[Event], None]


class EventBus:
    """Thread-safe event bus for parser→hook communication.

    Parsers publish events, hooks subscribe to events and perform graph
    operations in response.
    """

    def __init__(self) -> None:
        """Initialize event bus with empty subscriber registry."""
        self._subscribers: Dict[EventType, List[EventHandler]] = defaultdict(list)
        self._lock = RLock()
        logger.info("Event bus initialized")

    def subscribe(
        self, event_type: EventType, handler: EventHandler, name: Optional[str] = None
    ) -> None:
        """Subscribe to events of a specific type.

        Args:
            event_type: Type of event to subscribe to.
            handler: Callback function invoked when event is published.
            name: Optional handler name for logging.
        """
        with self._lock:
            self._subscribers[event_type].append(handler)
            handler_name = name or handler.__name__
            logger.debug(
                "Handler %s subscribed to %s", handler_name, event_type.name
            )

    def publish(self, event: Event) -> None:
        """Publish event to all registered subscribers.

        Args:
            event: Event to publish.
        """
        with self._lock:
            handlers = self._subscribers.get(event.event_type, [])
            if not handlers:
                logger.debug("No handlers for event %s", event.event_type.name)
                return

            logger.debug(
                "Publishing event %s to %d handlers", event.event_type.name, len(handlers)
            )
            for handler in handlers:
                try:
                    handler(event)
                except Exception as e:
                    logger.error(
                        "Handler %s failed for event %s: %s",
                        handler.__name__,
                        event.event_type.name,
                        e,
                    )

    def clear_subscribers(self, event_type: Optional[EventType] = None) -> None:
        """Clear subscribers for a specific event type or all types.

        Args:
            event_type: Event type to clear. If None, clears all subscribers.
        """
        with self._lock:
            if event_type is None:
                self._subscribers.clear()
                logger.debug("Cleared all event subscribers")
            else:
                self._subscribers[event_type].clear()
                logger.debug("Cleared subscribers for %s", event_type.name)
