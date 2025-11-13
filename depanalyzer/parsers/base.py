"""Base detector and parser interfaces for the new architecture.

Following AGENTS.md design: parsers split into detector (嗅探) and parser (解析) phases.
"""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional, Set

from graph.manager import GraphManager
from runtime.eventbus import Event, EventBus

logger = logging.getLogger("depanalyzer.parsers.base")


class BaseDetector(ABC):
    """Base class for target detectors.

    Detectors perform lightweight scanning to identify parsing targets
    (e.g., CMakeLists.txt, hvigorfile.ts) and publish detection events.
    """

    NAME: str = "base"

    def __init__(self, workspace_root: Path, eventbus: EventBus) -> None:
        """Initialize detector.

        Args:
            workspace_root: Workspace root path.
            eventbus: Event bus for publishing detection events.
        """
        self.workspace_root = workspace_root
        self.eventbus = eventbus
        logger.debug("Detector %s initialized", self.NAME)

    @abstractmethod
    def detect(self) -> List[Path]:
        """Detect parsing targets in workspace.

        Returns:
            List[Path]: List of detected target paths.
        """
        raise NotImplementedError

    def publish_detection_event(self, event: Event) -> None:
        """Publish detection event to event bus.

        Args:
            event: Detection event.
        """
        self.eventbus.publish(event)


class BaseParser(ABC):
    """Base class for parsers.

    Parsers consume detection events and perform detailed parsing,
    publishing structured events for hook consumption.
    """

    NAME: str = "base"

    def __init__(
        self,
        workspace_root: Path,
        graph_manager: GraphManager,
        eventbus: EventBus,
    ) -> None:
        """Initialize parser.

        Args:
            workspace_root: Workspace root path.
            graph_manager: Transaction graph manager.
            eventbus: Event bus for publishing parse events.
        """
        self.workspace_root = workspace_root
        self.graph_manager = graph_manager
        self.eventbus = eventbus
        logger.debug("Parser %s initialized", self.NAME)

    @abstractmethod
    def parse(self, target_path: Path) -> None:
        """Parse a detected target.

        Args:
            target_path: Path to parsing target.
        """
        raise NotImplementedError

    def publish_parse_event(self, event: Event) -> None:
        """Publish parse event to event bus.

        Args:
            event: Parse event.
        """
        self.eventbus.publish(event)

    def add_node(self, node_id: str, node_type: str, **attributes) -> None:
        """Helper to add node to transaction graph.

        Args:
            node_id: Node identifier.
            node_type: Node type.
            **attributes: Additional attributes.
        """
        self.graph_manager.add_node(node_id, node_type, **attributes)

    def add_edge(
        self, source: str, target: str, edge_kind: str, **attributes
    ) -> None:
        """Helper to add edge to transaction graph.

        Args:
            source: Source node ID.
            target: Target node ID.
            edge_kind: Edge kind.
            **attributes: Additional attributes.
        """
        self.graph_manager.add_edge(source, target, edge_kind, **attributes)
