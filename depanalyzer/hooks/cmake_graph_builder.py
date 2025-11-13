"""CMake Graph Builder Hook.

Subscribes to CMake parser events and builds the dependency graph based on
extracted facts. This separates fact extraction (in parsers/command handlers)
from graph construction (in hooks).
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from depanalyzer.runtime.eventbus import Event, EventType, EventBus
from depanalyzer.graph.manager import GraphManager

log = logging.getLogger("depanalyzer.hooks.cmake_graph_builder")


class CMakeGraphBuilder:
    """Hook that constructs graph nodes and edges from CMake parser events.

    This hook implements the event-driven architecture where parsers extract
    facts and publish events, while hooks perform graph operations based on
    those facts.
    """

    def __init__(self, graph_manager: GraphManager, eventbus: EventBus):
        """Initialize the CMake graph builder hook.

        Args:
            graph_manager: Graph manager for adding nodes and edges.
            eventbus: Event bus for subscribing to events.
        """
        self.graph_manager = graph_manager
        self.eventbus = eventbus
        self._register_handlers()

    def _register_handlers(self) -> None:
        """Register event handlers with the event bus."""
        self.eventbus.subscribe(
            EventType.CMAKE_TARGET_CREATED,
            self._handle_target_created,
            name="cmake_graph_builder.target_created",
        )
        self.eventbus.subscribe(
            EventType.CMAKE_SOURCE_FILES_ADDED,
            self._handle_source_files_added,
            name="cmake_graph_builder.source_files_added",
        )
        self.eventbus.subscribe(
            EventType.CMAKE_LINK_DEPENDENCY_FOUND,
            self._handle_link_dependency_found,
            name="cmake_graph_builder.link_dependency_found",
        )
        self.eventbus.subscribe(
            EventType.CMAKE_INCLUDE_DIRS_DECLARED,
            self._handle_include_dirs_declared,
            name="cmake_graph_builder.include_dirs_declared",
        )
        self.eventbus.subscribe(
            EventType.CMAKE_EXTERNAL_PACKAGE_REFERENCED,
            self._handle_external_package_referenced,
            name="cmake_graph_builder.external_package_referenced",
        )
        log.info("CMakeGraphBuilder registered event handlers")

    def _handle_target_created(self, event: Event) -> None:
        """Handle CMAKE_TARGET_CREATED event by creating target node.

        Args:
            event: Event containing target data.
        """
        data = event.data
        target_id = data["target_id"]
        node_type = data["node_type"]

        # Create target node
        node_attrs = {
            "node_type": node_type,
            "parser_name": event.source,
            "src_path": data.get("src_path"),
            "id": target_id,
            "origin": data.get("origin", "in_repo"),
            "provenance": data.get("provenance", "cmake_add_target"),
            "declared_via": data.get("declared_via"),
            "confidence": data.get("confidence", 1.0),
        }

        # Add optional attributes
        if data.get("imported") is not None:
            node_attrs["imported"] = data["imported"]
        if data.get("linkage_kind"):
            node_attrs["linkage_kind"] = data["linkage_kind"]
        if data.get("alias_of"):
            node_attrs["alias_of"] = data["alias_of"]

        self.graph_manager.add_node(target_id, **node_attrs)
        log.debug("Created target node: %s (type=%s)", target_id, node_type)

        # Handle alias edge if present
        if data.get("alias_edge"):
            alias_edge = data["alias_edge"]
            self.graph_manager.add_edge(
                alias_edge["source"],
                alias_edge["target"],
                edge_kind=alias_edge["edge_kind"],
                parser_name=event.source,
                confidence=1.0,
            )
            log.debug("Created alias edge: %s -> %s", alias_edge["source"], alias_edge["target"])

    def _handle_source_files_added(self, event: Event) -> None:
        """Handle CMAKE_SOURCE_FILES_ADDED event by creating source nodes and edges.

        Args:
            event: Event containing source files data.
        """
        data = event.data
        target_id = data["target_id"]
        source_files = data["source_files"]
        confidence = data.get("confidence", 1.0)

        for source_data in source_files:
            source_id = source_data["source_id"]
            node_type = source_data.get("node_type", "code")

            # Create source node
            self.graph_manager.add_node(
                source_id,
                node_type=node_type,
                parser_name=event.source,
                id=source_id,
                confidence=confidence,
            )

            # Create source edge
            self.graph_manager.add_edge(
                target_id,
                source_id,
                edge_kind="sources",
                parser_name=event.source,
                confidence=confidence,
            )

        log.debug("Added %d source files to target %s", len(source_files), target_id)

    def _handle_link_dependency_found(self, event: Event) -> None:
        """Handle CMAKE_LINK_DEPENDENCY_FOUND event by creating link edges.

        Args:
            event: Event containing link dependency data.
        """
        data = event.data
        source_id = data["source_id"]
        link_dependencies = data["link_dependencies"]
        confidence = data.get("confidence", 1.0)
        over_approx = data.get("over_approx", False)

        for dep_data in link_dependencies:
            target_id = dep_data["target_id"]

            self.graph_manager.add_edge(
                source_id,
                target_id,
                edge_kind="link_libraries",
                parser_name=event.source,
                confidence=confidence,
                over_approx=over_approx,
            )

        log.debug("Added %d link dependencies for %s", len(link_dependencies), source_id)

    def _handle_include_dirs_declared(self, event: Event) -> None:
        """Handle CMAKE_INCLUDE_DIRS_DECLARED event by updating include_dirs attribute.

        Args:
            event: Event containing include directories data.
        """
        data = event.data
        target_id = data["target_id"]
        new_dirs = data["include_dirs"]
        existing_dirs = data.get("existing_include_dirs", [])

        # Merge include directories (deduplicate)
        merged_dirs = []
        seen = set()

        # Add existing dirs first
        if isinstance(existing_dirs, list):
            for d in existing_dirs:
                if isinstance(d, str) and d not in seen:
                    merged_dirs.append(d)
                    seen.add(d)

        # Add new dirs
        for d in new_dirs:
            if d not in seen:
                merged_dirs.append(d)
                seen.add(d)

        # Ensure node exists (create placeholder if needed)
        if not self.graph_manager.has_node(target_id):
            self.graph_manager.add_node(
                target_id,
                node_type="artifact",
                parser_name=event.source,
                id=target_id,
                confidence=0.8,
            )

        # Update include_dirs attribute using the new API
        self.graph_manager.update_node_attribute(target_id, "include_dirs", merged_dirs)
        log.debug("Updated include_dirs for %s (%d total dirs)", target_id, len(merged_dirs))

    def _handle_external_package_referenced(self, event: Event) -> None:
        """Handle CMAKE_EXTERNAL_PACKAGE_REFERENCED event by creating external library node.

        Args:
            event: Event containing external package data.
        """
        data = event.data
        lib_id = data["lib_id"]
        node_type = data.get("node_type", "external_library")

        # Create external library node
        node_attrs = {
            "node_type": node_type,
            "parser_name": event.source,
            "id": lib_id,
            "origin": data.get("origin", "external"),
            "provenance": data.get("provenance"),
            "declared_via": data.get("declared_via"),
            "confidence": data.get("confidence", 1.0),
            "name": data.get("name"),
        }

        # Add optional attributes
        if data.get("version"):
            node_attrs["version"] = data["version"]
        if data.get("required") is not None:
            node_attrs["required"] = data["required"]
        if data.get("git_repository"):
            node_attrs["git_repository"] = data["git_repository"]
        if data.get("git_tag"):
            node_attrs["git_tag"] = data["git_tag"]

        self.graph_manager.add_node(lib_id, **node_attrs)
        log.debug("Created external library node: %s", lib_id)
