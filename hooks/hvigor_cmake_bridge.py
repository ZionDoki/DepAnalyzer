"""Hvigor↔CMake bridge hook for cross-domain association.

This hook subscribes to Hvigor and CMake parse events and establishes
connections between Hvigor modules and CMake targets using multiple抓手:
1. Path抓手: Hvigor native directory contains CMakeLists.txt
2. Artifact抓手: CMake output artifact name/ABI matches Hvigor input
3. Config抓手: OHOS toolchain/ABI consistency
4. Declaration抓手: Explicit native build config in Hvigor
"""

import logging
from pathlib import Path
from typing import Dict, List, Set

from graph.manager import EdgeKind, GraphManager, NodeType
from runtime.eventbus import Event, EventBus, EventType

logger = logging.getLogger("depanalyzer.hooks.hvigor_cmake_bridge")


class HvigorCMakeBridge:
    """Hook for associating Hvigor modules with CMake targets.

    Uses multiple evidence sources (path, artifact, config, declaration)
    to establish connections with confidence scoring.
    """

    def __init__(self, graph_manager: GraphManager, eventbus: EventBus) -> None:
        """Initialize bridge hook.

        Args:
            graph_manager: Transaction graph manager.
            eventbus: Event bus for subscribing to parse events.
        """
        self.graph_manager = graph_manager
        self.eventbus = eventbus

        # Track parsed entities
        self.hvigor_modules: Dict[str, Dict] = {}  # module_id -> module_data
        self.cmake_targets: Dict[str, Dict] = {}  # target_id -> target_data
        self.cmake_artifacts: Dict[str, Dict] = {}  # artifact_id -> artifact_data

        # Subscribe to relevant events
        self._subscribe_events()

        logger.info("HvigorCMakeBridge hook initialized")

    def _subscribe_events(self) -> None:
        """Subscribe to Hvigor and CMake parse events."""
        self.eventbus.subscribe(
            EventType.MODULE_PARSED, self._on_hvigor_module_parsed, "HvigorCMakeBridge"
        )
        self.eventbus.subscribe(
            EventType.CMAKE_TARGET_COMPLETED,
            self._on_cmake_target_completed,
            "HvigorCMakeBridge",
        )
        self.eventbus.subscribe(
            EventType.HVIGOR_NATIVE_DIR_FOUND,
            self._on_hvigor_native_dir,
            "HvigorCMakeBridge",
        )

    def _on_hvigor_module_parsed(self, event: Event) -> None:
        """Handle Hvigor module parse completion event.

        Args:
            event: Module parsed event.
        """
        module_id = event.data.get("module_id")
        if not module_id:
            return

        self.hvigor_modules[module_id] = event.data
        logger.debug("Recorded Hvigor module: %s", module_id)

    def _on_cmake_target_completed(self, event: Event) -> None:
        """Handle CMake target completion event.

        Args:
            event: Target completed event.
        """
        target_id = event.data.get("target_id")
        if not target_id:
            return

        self.cmake_targets[target_id] = event.data
        logger.debug("Recorded CMake target: %s", target_id)

    def _on_hvigor_native_dir(self, event: Event) -> None:
        """Handle Hvigor native directory detection event.

        Args:
            event: Native dir found event.
        """
        module_id = event.data.get("module_id")
        native_dir = event.data.get("native_dir")

        if not module_id or not native_dir:
            return

        # Store native dir association
        if module_id in self.hvigor_modules:
            self.hvigor_modules[module_id]["native_dir"] = native_dir

        logger.debug("Recorded native dir for %s: %s", module_id, native_dir)

    def execute(self) -> None:
        """Execute hook to establish Hvigor↔CMake associations.

        This is called during the JOIN phase after all parsing is complete.
        """
        logger.info("Executing HvigorCMakeBridge hook")

        # Attempt associations using multiple抓手
        associations = []

        # Path抓手: Match by directory containment
        path_matches = self._match_by_path()
        associations.extend(path_matches)

        # Artifact抓手: Match by output artifact names
        artifact_matches = self._match_by_artifact()
        associations.extend(artifact_matches)

        # Config抓手: Match by toolchain/ABI
        config_matches = self._match_by_config()
        associations.extend(config_matches)

        # Apply associations to graph
        for assoc in associations:
            self._create_association_edges(assoc)

        logger.info("Created %d Hvigor↔CMake associations", len(associations))

    def _match_by_path(self) -> List[Dict]:
        """Match Hvigor modules to CMake targets by path containment.

        Returns:
            List[Dict]: List of association records.
        """
        matches = []

        for module_id, module_data in self.hvigor_modules.items():
            native_dir = module_data.get("native_dir")
            if not native_dir:
                continue

            # Find CMake targets within this native directory
            for target_id, target_data in self.cmake_targets.items():
                target_path = target_data.get("src_path", "")
                if target_path.startswith(native_dir):
                    matches.append(
                        {
                            "module_id": module_id,
                            "target_id": target_id,
                            "method": "path",
                            "confidence": 0.9,
                            "evidence": [f"path:{native_dir}"],
                        }
                    )

        logger.debug("Path抓手 found %d matches", len(matches))
        return matches

    def _match_by_artifact(self) -> List[Dict]:
        """Match by artifact names/ABI.

        Returns:
            List[Dict]: List of association records.
        """
        # TODO: Implement artifact matching logic
        logger.debug("Artifact抓手 not yet implemented")
        return []

    def _match_by_config(self) -> List[Dict]:
        """Match by toolchain/config consistency.

        Returns:
            List[Dict]: List of association records.
        """
        # TODO: Implement config matching logic
        logger.debug("Config抓手 not yet implemented")
        return []

    def _create_association_edges(self, assoc: Dict) -> None:
        """Create graph edges for an association.

        Args:
            assoc: Association record.
        """
        module_id = assoc["module_id"]
        target_id = assoc["target_id"]
        confidence = assoc["confidence"]
        evidence = assoc.get("evidence", [])

        # Create NativeBuild process node if needed
        native_build_id = f"{module_id}:native_build"
        if not self.graph_manager.has_node(native_build_id):
            self.graph_manager.add_node(
                native_build_id,
                NodeType.PROCESS,
                parser_name="hvigor_cmake_bridge",
                process_type="native_build",
            )

        # Connect module -> NativeBuild -> CMake target
        self.graph_manager.add_edge(
            module_id,
            native_build_id,
            EdgeKind.DEPENDS_ON,
            confidence=confidence,
            evidence=evidence,
            parser_name="hvigor_cmake_bridge",
        )

        self.graph_manager.add_edge(
            native_build_id,
            target_id,
            EdgeKind.DEPENDS_ON,
            confidence=confidence,
            evidence=evidence,
            parser_name="hvigor_cmake_bridge",
        )

        logger.debug(
            "Created association: %s -> %s (confidence=%.2f)",
            module_id,
            target_id,
            confidence,
        )
