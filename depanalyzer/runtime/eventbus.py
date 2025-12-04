"""Event bus for parser→graph wiring communication.

Parsers publish structured events (for example, targets created, sources
attached, dependencies discovered), and dedicated graph builders or
collectors subscribe to these events to perform cross-domain
associations.
"""

# Event handlers intentionally catch unexpected errors so one bad subscriber does not crash the bus.


import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from threading import RLock
from typing import Any, Callable, Dict, List, Optional, Tuple

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

    Key improvements:
    - Handlers are executed outside the lock to prevent blocking
    - Subscription IDs enable targeted unsubscription
    - Handler names are stored for better debugging
    """

    def __init__(self) -> None:
        """Initialize event bus with empty subscriber registry."""
        # Subscribers stored as (subscription_id, handler, name) tuples
        self._subscribers: Dict[EventType, List[Tuple[str, EventHandler, str]]] = defaultdict(list)
        self._lock = RLock()
        logger.info("Event bus initialized")

    def subscribe(
        self, event_type: EventType, handler: EventHandler, name: Optional[str] = None
    ) -> str:
        """Subscribe to events of a specific type.

        Args:
            event_type: Type of event to subscribe to.
            handler: Callback function invoked when event is published.
            name: Optional handler name for logging.

        Returns:
            str: Subscription ID that can be used to unsubscribe.
        """
        subscription_id = str(uuid.uuid4())
        handler_name = name or getattr(handler, "__name__", "anonymous")

        with self._lock:
            self._subscribers[event_type].append((subscription_id, handler, handler_name))
            logger.debug(
                "Handler %s subscribed to %s (id=%s)",
                handler_name,
                event_type.name,
                subscription_id[:8],
            )

        return subscription_id

    def unsubscribe(self, subscription_id: str) -> bool:
        """Unsubscribe a handler by its subscription ID.

        Args:
            subscription_id: The ID returned by subscribe().

        Returns:
            bool: True if the subscription was found and removed.
        """
        with self._lock:
            for event_type, handlers in self._subscribers.items():
                for i, (sid, _, handler_name) in enumerate(handlers):
                    if sid == subscription_id:
                        del handlers[i]
                        logger.debug(
                            "Handler %s unsubscribed from %s (id=%s)",
                            handler_name,
                            event_type.name,
                            subscription_id[:8],
                        )
                        return True
        return False

    def publish(self, event: Event) -> None:
        """Publish event to all registered subscribers.

        Handlers are executed outside the lock to prevent blocking other
        publish/subscribe operations during handler execution.

        Args:
            event: Event to publish.
        """
        # Copy handlers while holding lock, then execute outside lock
        with self._lock:
            handlers_snapshot = list(self._subscribers.get(event.event_type, []))

        if not handlers_snapshot:
            logger.debug("No handlers for event %s", event.event_type.name)
            return

        logger.debug(
            "Publishing event %s to %d handlers",
            event.event_type.name,
            len(handlers_snapshot),
        )

        # Execute handlers outside the lock
        for subscription_id, handler, handler_name in handlers_snapshot:
            self._safe_call_handler(handler, handler_name, event)

    def _safe_call_handler(
        self, handler: EventHandler, handler_name: str, event: Event
    ) -> None:
        """Safely call a handler, catching and logging exceptions.

        Args:
            handler: The handler function to call.
            handler_name: Name of the handler for logging.
            event: The event to pass to the handler.
        """
        try:
            handler(event)
        except Exception as e:
            # Log with stack trace for debugging
            logger.error(
                "Handler %s failed for event %s: %s",
                handler_name,
                event.event_type.name,
                e,
                exc_info=True,
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

    def subscriber_count(self, event_type: Optional[EventType] = None) -> int:
        """Get the number of subscribers.

        Args:
            event_type: Event type to count. If None, counts all subscribers.

        Returns:
            int: Number of subscribers.
        """
        with self._lock:
            if event_type is None:
                return sum(len(handlers) for handlers in self._subscribers.values())
            return len(self._subscribers.get(event_type, []))
