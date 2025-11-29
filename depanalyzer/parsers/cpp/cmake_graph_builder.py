"""CMake graph builder for the C/C++ ecosystem.

This component subscribes to CMake parser events and constructs graph
nodes and edges based on the extracted facts. It implements the
event-driven architecture where command handlers publish events and
the graph builder performs graph operations.
"""

# Event processing catches broad exceptions so a single bad event does not stop parsing.


from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from depanalyzer.graph.contract import BuildInterfaceContract, ContractType
from depanalyzer.graph.contract_registry import ContractRegistry
from depanalyzer.graph import EdgeKind, GraphManager, NodeType
from depanalyzer.graph import EdgeSpec, NodeSpec
from depanalyzer.runtime.eventbus import Event, EventBus, EventType

log = logging.getLogger("depanalyzer.parsers.cpp.cmake_graph_builder")


class CMakeGraphBuilder:
    """Construct graph nodes and edges from CMake parser events."""

    def __init__(self, graph_manager: GraphManager, eventbus: EventBus):
        """Initialize the CMake graph builder.

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
        """Handle CMAKE_TARGET_CREATED by creating a target node."""
        data = event.data
        target_id = data["target_id"]
        node_type = data["node_type"]
        confidence = data.get("confidence", 1.0)
        target_name = data.get("target_name") or target_id.split(":")[-1]

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
        self.graph_manager.add_node_spec(node_spec)
        log.debug("Created target node: %s (type=%s)", target_id, ntype.value)

        # Create a logical output node produced by this target so downstream
        # consumers (Hvigor/Hap packaging, etc.) can reference build outputs.
        artifact_id = f"{target_id}:artifact"
        output_type = ntype if ntype in (
            NodeType.SHARED_LIBRARY,
            NodeType.STATIC_LIBRARY,
            NodeType.EXECUTABLE,
        ) else NodeType.TARGET
        artifact_spec = NodeSpec(
            id=artifact_id,
            type=output_type,
            label=artifact_id,
            parser_name=event.source,
            confidence=confidence,
            attrs={
                "produced_by": target_id,
                "target_type": ntype.value,
                "name": target_name,
                "linkage_kind": attrs.get("linkage_kind"),
            },
        )
        self.graph_manager.add_node_spec(artifact_spec)
        self.graph_manager.add_edge(
            target_id,
            artifact_id,
            edge_kind=EdgeKind.PRODUCES.value,
            parser_name=event.source,
            confidence=confidence,
        )
        log.debug("Created output node for target %s -> %s (type=%s)", target_id, artifact_id, output_type.value)

        self._register_provider_contracts(
            target_id=target_id,
            target_name=target_name,
            node_type=ntype,
            raw_node_type=data.get("node_type"),
            src_path=data.get("src_path"),
            provenance=data.get("provenance"),
            declared_via=data.get("declared_via"),
            is_imported=bool(data.get("imported")),
            is_alias=bool(data.get("is_alias")),
        )

        # Handle alias edge if present.
        if data.get("alias_edge"):
            alias_edge = data["alias_edge"]
            self.graph_manager.add_edge(
                alias_edge["source"],
                alias_edge["target"],
                edge_kind=EdgeKind.ALIAS_OF.value,
                parser_name=event.source,
                confidence=1.0,
            )
            log.debug(
                "Created alias edge: %s -> %s",
                alias_edge["source"],
                alias_edge["target"],
            )

    def _register_provider_contracts(
        self,
        target_id: str,
        target_name: str,
        node_type: NodeType,
        raw_node_type: str | None,
        src_path: str | None,
        provenance: str | None,
        declared_via: str | None,
        is_imported: bool,
        is_alias: bool,
    ) -> None:
        """Register provider-side contracts for CMake targets that build libraries."""
        if is_imported or is_alias:
            return

        is_module_library = raw_node_type == "module_library"
        if node_type not in (NodeType.SHARED_LIBRARY, NodeType.STATIC_LIBRARY) and not is_module_library:
            return

        if not src_path:
            log.debug("Skipping provider contract registration for %s (no src_path)", target_id)
            return

        root_path = self.graph_manager.root_path
        if root_path is None:
            log.debug("Skipping provider contract registration for %s (no graph root_path)", target_id)
            return

        cmake_dir = Path(src_path).resolve().parent
        artifact_name = f"lib{target_name}.so"

        candidate_dirs = [
            cmake_dir / "build",
            cmake_dir / "lib",
            cmake_dir / "out",
            cmake_dir,
        ]

        registry = ContractRegistry.get_instance()
        provider_count = 0
        seen_providers: set[str] = set()

        for candidate_dir in candidate_dirs:
            try:
                provider_path = candidate_dir / artifact_name
                normalized = self.graph_manager.normalize_path(provider_path)
            except ValueError:
                log.debug(
                    "Could not normalize provider path for %s under %s", target_id, candidate_dir
                )
                continue
            if normalized in seen_providers:
                continue
            seen_providers.add(normalized)

            evidence = [
                f"cmake_target:{target_name}",
                f"cmake_dir:{cmake_dir}",
            ]
            if declared_via:
                evidence.append(f"declared_via:{declared_via}")
            if provenance:
                evidence.append(f"provenance:{provenance}")

            contract = BuildInterfaceContract(
                artifact_name=artifact_name,
                provider_artifact=normalized,
                consumer_artifact="",
                contract_type=ContractType.ARTIFACT_NAME,
                confidence=0.9,
                evidence=evidence,
                metadata={
                    "target_id": target_id,
                    "native_dir": str(cmake_dir),
                },
            )
            registry.register(contract)
            provider_count += 1

        if provider_count:
            log.info(
                "Registered %d provider contract(s) for CMake target %s",
                provider_count,
                target_id,
            )

    def _handle_source_files_added(self, event: Event) -> None:
        """Handle CMAKE_SOURCE_FILES_ADDED by attaching sources to a target."""
        data = event.data
        target_id = data["target_id"]
        source_files = data["source_files"]
        confidence = data.get("confidence", 1.0)

        for source_id in source_files:
            node_type = NodeType.CODE
            if isinstance(source_id, dict):
                node_type = NodeType(source_id.get("node_type", "code")) \
                    if source_id.get("node_type") in NodeType._value2member_map_ \
                    else NodeType.CODE
                source_id = source_id.get("source_id")

            if not isinstance(source_id, str) or not source_id:
                continue

            try:
                ntype = node_type
            except ValueError:
                ntype = NodeType.CODE

            node_spec = NodeSpec(
                id=source_id,
                type=ntype,
                label=source_id,
                src_path=None,
                parser_name=event.source,
                confidence=confidence,
                attrs={},
            )
            self.graph_manager.add_node_spec(node_spec)

            self.graph_manager.add_edge(
                target_id,
                source_id,
                edge_kind=EdgeKind.SOURCES.value,
                parser_name=event.source,
                confidence=confidence,
            )

        log.debug("Added %d source files to target %s", len(source_files), target_id)

    def _handle_link_dependency_found(self, event: Event) -> None:
        """Handle CMAKE_LINK_DEPENDENCY_FOUND by creating link edges."""
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

        log.debug(
            "Added %d link dependencies for %s", len(link_dependencies), source_id
        )

    def _handle_include_dirs_declared(self, event: Event) -> None:
        """Handle CMAKE_INCLUDE_DIRS_DECLARED by updating include_dirs."""
        data = event.data
        target_id = data["target_id"]
        new_dirs = data["include_dirs"]
        existing_dirs = data.get("existing_include_dirs", [])

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

        if not self.graph_manager.has_node(target_id):
            node_spec = NodeSpec(
                id=target_id,
                type=NodeType.TARGET,
                label=target_id,
                parser_name=event.source,
                confidence=data.get("confidence", 1.0),
                provisional=True,
                attrs={"id": target_id},
            )
            self.graph_manager.add_node_spec(node_spec)

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

        config_edge = EdgeSpec(
            source=target_id,
            target=config_id,
            kind=EdgeKind.DEFINED_BY,
            parser_name=event.source,
        )
        self.graph_manager.add_edge_spec(config_edge)

        root_path = self.graph_manager.root_path
        if root_path is not None:
            for rel_dir in merged_dirs:
                try:
                    abs_dir = (root_path / rel_dir).resolve()
                except Exception:
                    continue

                if not abs_dir.is_dir():
                    continue

                dir_id = f"//{rel_dir}"
                if not self.graph_manager.has_node(dir_id):
                    subdir_spec = NodeSpec(
                        id=dir_id,
                        type=NodeType.SUBDIRECTORY,
                        label=dir_id,
                        src_path=str(abs_dir),
                        parser_name=event.source,
                        confidence=1.0,
                        attrs={"id": dir_id},
                    )
                    self.graph_manager.add_node_spec(subdir_spec)

                include_edge = EdgeSpec(
                    source=config_id,
                    target=dir_id,
                    kind=EdgeKind.INCLUDE_DIRS,
                    parser_name=event.source,
                )
                self.graph_manager.add_edge_spec(include_edge)

    def _handle_external_package_referenced(self, event: Event) -> None:
        """Handle CMAKE_EXTERNAL_PACKAGE_REFERENCED by registering contracts."""
        data = event.data
        provider_artifact = data.get("provider_artifact")
        consumer_artifact = data.get("consumer_artifact")
        artifact_name = data.get("artifact_name")

        if not provider_artifact or not artifact_name:
            return

        registry = ContractRegistry.get_instance()
        contract = BuildInterfaceContract(
            artifact_name=artifact_name,
            provider_artifact=provider_artifact,
            consumer_artifact=consumer_artifact,
            contract_type=ContractType.ARTIFACT_NAME,
            confidence=data.get("confidence", 0.9),
        )
        registry.register(contract)
        log.debug(
            "Registered provider contract: %s (artifact=%s)",
            artifact_name,
            provider_artifact,
        )


__all__ = ["CMakeGraphBuilder"]
