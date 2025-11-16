"""CMake Graph Builder Hook.

Subscribes to CMake parser events and builds the dependency graph based on
extracted facts. This separates fact extraction (in parsers/command handlers)
from graph construction (in hooks).
"""

from __future__ import annotations

import logging
from typing import Any, Dict
from pathlib import Path

from depanalyzer.runtime.eventbus import Event, EventType, EventBus
from depanalyzer.graph.manager import GraphManager, NodeType, EdgeKind
from depanalyzer.graph.schema import NodeSpec, EdgeSpec
from depanalyzer.graph.contract import BuildInterfaceContract, ContractType
from depanalyzer.graph.contract_registry import ContractRegistry

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

        # Create target node using the unified NodeSpec schema
        confidence = data.get("confidence", 1.0)

        try:
            ntype = NodeType(node_type)
        except ValueError:
            ntype = NodeType.TARGET

        attrs: Dict[str, Any] = {
            "origin": data.get("origin", "in_repo"),
            "provenance": data.get("provenance", "cmake_add_target"),
            "declared_via": data.get("declared_via"),
        }
        if data.get("imported") is not None:
            attrs["imported"] = data["imported"]
        if data.get("linkage_kind"):
            attrs["linkage_kind"] = data["linkage_kind"]
        if data.get("alias_of"):
            attrs["alias_of"] = data["alias_of"]

        node_spec = NodeSpec(
            id=target_id,
            type=ntype,
            label=target_id,
            src_path=data.get("src_path"),
            parser_name=event.source,
            confidence=confidence,
            attrs=attrs,
        )
        self.graph_manager.add_node(
            node_spec.id,
            node_spec.type.value,
            confidence=node_spec.confidence,
            **node_spec.to_backend_attrs(),
        )
        log.debug("Created target node: %s (type=%s)", target_id, ntype.value)

        # Handle alias edge if present
        if data.get("alias_edge"):
            alias_edge = data["alias_edge"]
            self.graph_manager.add_edge(
                alias_edge["source"],
                alias_edge["target"],
                edge_kind=EdgeKind.ALIAS_OF.value,
                parser_name=event.source,
                confidence=1.0,
            )
            log.debug("Created alias edge: %s -> %s", alias_edge["source"], alias_edge["target"])

        # Register provider-side contract for shared libraries
        # This enables cross-language matching with Hvigor consumers
        if node_type == "shared_library" and data.get("origin") == "in_repo":
            self._register_provider_contract(data, target_id)

    def _register_provider_contract(self, target_data: Dict[str, Any], target_id: str) -> None:
        """Register provider-side contract for a CMake shared library target.

        Args:
            target_data: Target data from CMAKE_TARGET_CREATED event
            target_id: Target node identifier
        """
        try:
            # Extract target name from target_id
            # target_id format is typically: //path/to/CMakeLists.txt:target_name
            target_name = target_id.split(":")[-1] if ":" in target_id else target_id.split("/")[-1]

            # Infer artifact output path
            # Typically: build/libname.so or lib/libname.so
            src_path = Path(target_data.get("src_path", ""))
            cmake_dir = src_path.parent if src_path else Path(".")

            # Try common build output directories
            for build_dir in ["build", "lib", "out", "."]:
                artifact_path = cmake_dir / build_dir / f"lib{target_name}.so"
                provider_artifact_id = self.graph_manager.normalize_path(artifact_path)

                # Create provider contract
                registry = ContractRegistry()
                contract = BuildInterfaceContract(
                    provider_artifact=provider_artifact_id,
                    consumer_artifact="",  # To be matched in JOIN phase
                    artifact_name=f"lib{target_name}.so",
                    contract_type=ContractType.ARTIFACT_NAME,
                    confidence=0.0,  # Will be set during matching
                    evidence=[
                        f"cmake_target:{target_name}",
                        f"src_path:{src_path}",
                        f"node_type:{target_data.get('node_type')}",
                    ],
                    impl_files=[],  # Will be populated by source file events
                    metadata={
                        "target_name": target_name,
                        "target_id": target_id,
                        "build_dir": build_dir,
                        "linkage_kind": target_data.get("linkage_kind", "shared"),
                    },
                )
                registry.register(contract)
                log.debug(
                    "Registered provider contract: %s (artifact=%s, build_dir=%s)",
                    target_name,
                    provider_artifact_id,
                    build_dir,
                )

        except Exception as contract_err:
            log.warning(
                "Failed to register provider contract for %s: %s",
                target_id,
                contract_err,
            )

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
            node_type = source_data.get("node_type", NodeType.CODE.value)

            try:
                ntype = NodeType(node_type)
            except ValueError:
                ntype = NodeType.CODE

            node_spec = NodeSpec(
                id=source_id,
                type=ntype,
                label=source_id,
                src_path=None,
                parser_name=event.source,
                confidence=confidence,
                attrs={"id": source_id},
            )
            self.graph_manager.add_node(
                node_spec.id,
                node_spec.type.value,
                confidence=node_spec.confidence,
                **node_spec.to_backend_attrs(),
            )

            # Create source edge using the canonical kind
            self.graph_manager.add_edge(
                target_id,
                source_id,
                edge_kind=EdgeKind.SOURCES.value,
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
        merged_dirs: list[str] = []
        seen = set()

        if isinstance(existing_dirs, list):
            for d in existing_dirs:
                if isinstance(d, str) and d not in seen:
                    merged_dirs.append(d)
                    seen.add(d)

        for d in new_dirs:
            if isinstance(d, str) and d not in seen:
                merged_dirs.append(d)
                seen.add(d)

        # Ensure target node exists (create placeholder if needed)
        if not self.graph_manager.has_node(target_id):
            node_spec = NodeSpec(
                id=target_id,
                type=NodeType.TARGET,
                label=target_id,
                parser_name=event.source,
                confidence=0.8,
                provisional=True,
                attrs={"id": target_id},
            )
            self.graph_manager.add_node_spec(node_spec)

        # Create a BUILD_CONFIG node representing this include_dirs configuration
        config_id = f"{target_id}:include_dirs"
        config_spec = NodeSpec(
            id=config_id,
            type=NodeType.BUILD_CONFIG,
            label=f"{target_id}#include_dirs",
            parser_name=event.source,
            confidence=data.get("confidence", 1.0),
            attrs={
                "config_kind": "include_dirs",
                "include_dirs": merged_dirs,
                "declared_via": "target_include_directories",
            },
        )
        self.graph_manager.add_node_spec(config_spec)

        # Link target -> BUILD_CONFIG
        config_edge = EdgeSpec(
            source=target_id,
            target=config_id,
            kind=EdgeKind.DEFINED_BY,
            parser_name=event.source,
        )
        self.graph_manager.add_edge_spec(config_edge)

        # Optionally: model include directories as SUBDIRECTORY nodes when
        # root_path is known, and connect BUILD_CONFIG -> SUBDIRECTORY via
        # INCLUDE_DIRS edges.
        root_path = self.graph_manager.root_path
        if root_path is not None:
            for rel_dir in merged_dirs:
                try:
                    abs_dir = (root_path / rel_dir).resolve()
                except Exception:
                    continue

                try:
                    dir_id = self.graph_manager.normalize_path(abs_dir)
                except Exception:
                    dir_id = f"//{rel_dir.lstrip('/')}"

                if not self.graph_manager.has_node(dir_id):
                    dir_spec = NodeSpec(
                        id=dir_id,
                        type=NodeType.SUBDIRECTORY,
                        label=rel_dir,
                        src_path=str(abs_dir),
                        parser_name=event.source,
                    )
                    self.graph_manager.add_node_spec(dir_spec)

                dir_edge = EdgeSpec(
                    source=config_id,
                    target=dir_id,
                    kind=EdgeKind.INCLUDE_DIRS,
                    parser_name=event.source,
                )
                self.graph_manager.add_edge_spec(dir_edge)

        # Keep target-level include_dirs attribute for compatibility with
        # existing projection logic.
        self.graph_manager.update_node_attribute(target_id, "include_dirs", merged_dirs)
        log.debug(
            "Updated include_dirs for %s (%d total dirs) via BUILD_CONFIG %s",
            target_id,
            len(merged_dirs),
            config_id,
        )

    def _handle_external_package_referenced(self, event: Event) -> None:
        """Handle CMAKE_EXTERNAL_PACKAGE_REFERENCED event by creating external library node.

        Args:
            event: Event containing external package data.
        """
        data = event.data
        lib_id = data["lib_id"]
        node_type = data.get("node_type", NodeType.EXTERNAL_LIBRARY.value)

        try:
            ntype = NodeType(node_type)
        except ValueError:
            ntype = NodeType.EXTERNAL_LIBRARY

        confidence = data.get("confidence", 1.0)

        attrs: Dict[str, Any] = {
            "origin": data.get("origin", "external"),
            "provenance": data.get("provenance"),
            "declared_via": data.get("declared_via"),
            "name": data.get("name"),
        }
        if data.get("version"):
            attrs["version"] = data["version"]
        if data.get("required") is not None:
            attrs["required"] = data["required"]
        if data.get("git_repository"):
            attrs["git_repository"] = data["git_repository"]
        if data.get("git_tag"):
            attrs["git_tag"] = data["git_tag"]

        node_spec = NodeSpec(
            id=lib_id,
            type=ntype,
            label=lib_id,
            parser_name=event.source,
            confidence=confidence,
            attrs=attrs,
        )
        self.graph_manager.add_node(
            node_spec.id,
            node_spec.type.value,
            confidence=node_spec.confidence,
            **node_spec.to_backend_attrs(),
        )
        log.debug("Created external library node: %s", lib_id)
