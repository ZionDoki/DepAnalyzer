"""Packaging bridge hook for connecting artifacts to packaging targets.

Connects final artifacts (CMake outputs, prebuilt libs) to packaging targets
(HAP/HAR/HSP) with ABI/variant filtering.
"""

import logging
from typing import Dict, List

from depanalyzer.graph.manager import EdgeKind, GraphManager
from depanalyzer.runtime.eventbus import Event, EventBus, EventType

logger = logging.getLogger("depanalyzer.hooks.packaging_bridge")


class PackagingBridge:
    """Hook for associating artifacts with packaging targets.

    Identifies final artifacts and creates 'contains' edges to
    packaging targets (HAP/HAR/HSP) with ABI filtering.
    """

    def __init__(self, graph_manager: GraphManager, eventbus: EventBus) -> None:
        """Initialize packaging bridge hook.

        Args:
            graph_manager: Transaction graph manager.
            eventbus: Event bus for subscribing to events.
        """
        self.graph_manager = graph_manager
        self.eventbus = eventbus

        self.artifacts: Dict[str, Dict] = {}
        self.packaging_targets: Dict[str, Dict] = {}

        self._subscribe_events()
        logger.info("PackagingBridge hook initialized")

    def _subscribe_events(self) -> None:
        """Subscribe to artifact and packaging events."""
        self.eventbus.subscribe(
            EventType.ARTIFACT_PRODUCED, self._on_artifact_produced, "PackagingBridge"
        )
        self.eventbus.subscribe(
            EventType.PACKAGING_BOUNDARY,
            self._on_packaging_boundary,
            "PackagingBridge",
        )

    def _on_artifact_produced(self, event: Event) -> None:
        """Handle artifact produced event.

        Args:
            event: Artifact produced event.
        """
        artifact_id = event.data.get("artifact_id")
        if artifact_id:
            self.artifacts[artifact_id] = event.data
            logger.debug("Recorded artifact: %s", artifact_id)

    def _on_packaging_boundary(self, event: Event) -> None:
        """Handle packaging boundary event.

        Args:
            event: Packaging boundary event.
        """
        target_id = event.data.get("target_id")
        if target_id:
            self.packaging_targets[target_id] = event.data
            logger.debug("Recorded packaging target: %s", target_id)

    def execute(self) -> None:
        """Execute hook to create packaging associations."""
        logger.info("Executing PackagingBridge hook")

        associations = self._match_artifacts_to_packages()

        for assoc in associations:
            self._create_contains_edge(assoc)

        logger.info("Created %d packaging associations", len(associations))

    def _match_artifacts_to_packages(self) -> List[Dict]:
        """Match artifacts to packaging targets.

        Returns:
            List[Dict]: List of association records.
        """
        matches = []

        # TODO: Implement actual matching logic with ABI filtering
        # For now, placeholder
        logger.debug("Artifact matching not yet fully implemented")

        return matches

    def _create_contains_edge(self, assoc: Dict) -> None:
        """Create contains edge for packaging association.

        Args:
            assoc: Association record.
        """
        package_id = assoc["package_id"]
        artifact_id = assoc["artifact_id"]
        abi = assoc.get("abi")

        self.graph_manager.add_edge(
            package_id,
            artifact_id,
            EdgeKind.CONTAINS,
            confidence=0.9,
            parser_name="packaging_bridge",
            **({"abi": abi} if abi else {}),
        )

        logger.debug("Created contains: %s -> %s", package_id, artifact_id)
