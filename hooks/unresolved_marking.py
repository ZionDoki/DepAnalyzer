"""Unresolved dependency marking hook.

Marks unresolved dependencies (dlopen, unknown libs) with over-approximation
and evidence flags.
"""

import logging

from graph.manager import GraphManager, NodeType
from runtime.eventbus import EventBus

logger = logging.getLogger("depanalyzer.hooks.unresolved_marking")


class UnresolvedMarking:
    """Hook for marking unresolved dependencies.

    Identifies dlopen calls, unresolved library names, and other
    uncertain dependencies, marking them with over_approx flag.
    """

    def __init__(self, graph_manager: GraphManager, eventbus: EventBus) -> None:
        """Initialize unresolved marking hook.

        Args:
            graph_manager: Transaction graph manager.
            eventbus: Event bus.
        """
        self.graph_manager = graph_manager
        self.eventbus = eventbus
        logger.info("UnresolvedMarking hook initialized")

    def execute(self) -> None:
        """Execute hook to mark unresolved dependencies."""
        logger.info("Executing UnresolvedMarking hook")

        marked_count = 0

        # Find external_dep nodes without resolved paths
        for node_id, attrs in self.graph_manager.nodes():
            if attrs.get("type") == NodeType.EXTERNAL_DEP:
                if not attrs.get("resolved_path"):
                    # Mark as over-approximation
                    # TODO: Update node attributes
                    marked_count += 1

        logger.info("Marked %d unresolved dependencies", marked_count)
