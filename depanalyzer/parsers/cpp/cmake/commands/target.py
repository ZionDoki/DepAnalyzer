"""Target command handler for add_library and add_executable commands."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Dict, Any

from depanalyzer.graph.identifiers import normalize_node_id
from depanalyzer.parsers.cpp.cmake.commands.base import CommandHandler
from depanalyzer.parsers.cpp.cmake.tokens import (
    clean_token,
    extract_dependencies_from_args,
    CMAKE_VAR_PATTERN,
)
from depanalyzer.parsers.cpp.cmake.variables import CMakeVariableResolver
from depanalyzer.graph.manager import GraphManager
from depanalyzer.runtime.eventbus import Event, EventType, EventBus

log = logging.getLogger("depanalyzer.parsers.cpp.cmake.commands.target")


def map_target_type(cmake_command: str, library_type: Optional[str] = None) -> str:
    """Map CMake command and library type to standardized node type.

    Args:
        cmake_command: The CMake command name (e.g., "add_library").
        library_type: Optional library type modifier (e.g., "SHARED", "STATIC").

    Returns:
        Standardized node type string.
    """
    if cmake_command == "add_executable":
        return "executable"
    elif cmake_command == "add_library":
        if library_type == "SHARED":
            return "shared_library"
        elif library_type == "STATIC":
            return "static_library"
        elif library_type == "MODULE":
            return "module_library"
        elif library_type == "INTERFACE":
            return "interface_library"
        else:
            # Safety-first: unknown defaults to static for compliance conservatism
            return "static_library"
    else:
        return "library"


class TargetCommandHandler(CommandHandler):
    """Handler for target creation commands (add_library, add_executable)."""

    HANDLED_COMMANDS = {"add_library", "add_executable"}

    def can_handle(self, command_name: str) -> bool:
        """Check if this handler can process the given command.

        Args:
            command_name: The lowercase command name.

        Returns:
            True if command is add_library or add_executable.
        """
        return command_name in self.HANDLED_COMMANDS

    def handle(
        self,
        command_name: str,
        args: List[str],
        file_path: Path,
        shared_graph: GraphManager,
        variable_resolver: CMakeVariableResolver,
    ) -> bool:
        """Handle target creation commands.

        Args:
            command_name: "add_library" or "add_executable".
            args: Command arguments.
            file_path: CMakeLists.txt path.
            shared_graph: Graph manager.
            variable_resolver: Variable resolver.

        Returns:
            True if the command was successfully processed.
        """
        if not args:
            return False

        target = clean_token(args[0])
        if not target:
            return False

        target = variable_resolver.resolve(target)

        lib_type = None
        source_start_idx = 1
        is_imported = False
        is_alias = False
        alias_of: Optional[str] = None

        if (
            command_name == "add_library"
            and len(args) >= 2
            and args[1].upper() in ("STATIC", "SHARED", "MODULE", "OBJECT", "INTERFACE")
        ):
            lib_type = args[1].upper()
            source_start_idx = 2

        up_args = [(clean_token(a) or "").upper() for a in args]
        if "IMPORTED" in up_args:
            is_imported = True
        if "ALIAS" in up_args and command_name == "add_library":
            is_alias = True
            try:
                alias_idx = up_args.index("ALIAS")
                if alias_idx + 1 < len(args):
                    alias_of = clean_token(args[alias_idx + 1])
            except ValueError:
                pass

        node_type = map_target_type(command_name, lib_type)
        target_id = normalize_node_id(file_path, self.repo_root, target)
        src_path = str(file_path.resolve())

        if is_alias and alias_of:
            self._handle_alias_target(
                target_id, alias_of, node_type, src_path, command_name,
                file_path, shared_graph, variable_resolver
            )
            return True

        self._create_target_node(
            target_id, node_type, src_path, command_name, is_imported, shared_graph
        )

        if not is_imported:
            self._add_source_files(
                target_id, args, source_start_idx, file_path,
                shared_graph, variable_resolver
            )

        return True

    def _handle_alias_target(
        self,
        target_id: str,
        alias_of: str,
        node_type: str,
        src_path: str,
        command_name: str,
        file_path: Path,
        shared_graph: GraphManager,
        variable_resolver: CMakeVariableResolver,
    ) -> None:
        """Handle alias target creation by publishing CMAKE_TARGET_CREATED event.

        Args:
            target_id: ID for the alias target.
            alias_of: Target that this is an alias of.
            node_type: Node type.
            src_path: Source file path.
            command_name: Command name.
            file_path: CMakeLists.txt path.
            shared_graph: Graph manager (unused, kept for interface compatibility).
            variable_resolver: Variable resolver.
        """
        alias_target_id = normalize_node_id(
            file_path,
            self.repo_root,
            variable_resolver.resolve(alias_of),
        )

        event = Event(
            event_type=EventType.CMAKE_TARGET_CREATED,
            source=self.parser_name,
            data={
                "target_id": target_id,
                "node_type": node_type,
                "src_path": src_path,
                "declared_via": command_name,
                "origin": "in_repo",
                "provenance": "cmake_add_target",
                "confidence": 1.0,
                "is_alias": True,
                "alias_of": alias_target_id,
                "alias_edge": {
                    "source": target_id,
                    "target": alias_target_id,
                    "edge_kind": "alias_of",
                },
            },
        )
        self.eventbus.publish(event)
        log.debug("Published CMAKE_TARGET_CREATED (alias) for %s -> %s", target_id, alias_target_id)

    def _create_target_node(
        self,
        target_id: str,
        node_type: str,
        src_path: str,
        command_name: str,
        is_imported: bool,
        shared_graph: GraphManager,
    ) -> None:
        """Create a target node by publishing CMAKE_TARGET_CREATED event.

        Args:
            target_id: Target identifier.
            node_type: Node type.
            src_path: Source file path.
            command_name: Command name.
            is_imported: Whether this is an imported target.
            shared_graph: Graph manager (unused, kept for interface compatibility).
        """
        linkage_kind = None
        if node_type == "shared_library":
            linkage_kind = "shared"
        elif node_type == "static_library":
            linkage_kind = "static"
        elif node_type == "executable":
            linkage_kind = "executable"

        event = Event(
            event_type=EventType.CMAKE_TARGET_CREATED,
            source=self.parser_name,
            data={
                "target_id": target_id,
                "node_type": node_type,
                "src_path": src_path,
                "declared_via": command_name,
                "origin": "external" if is_imported else "in_repo",
                "provenance": "cmake_add_target",
                "confidence": 1.0,
                "imported": is_imported or None,
                "linkage_kind": linkage_kind,
            },
        )
        self.eventbus.publish(event)
        log.debug("Published CMAKE_TARGET_CREATED for %s", target_id)

    def _add_source_files(
        self,
        target_id: str,
        args: List[str],
        source_start_idx: int,
        file_path: Path,
        shared_graph: GraphManager,
        variable_resolver: CMakeVariableResolver,
    ) -> None:
        """Add source file nodes and edges by publishing CMAKE_SOURCE_FILES_ADDED event.

        Args:
            target_id: Target identifier.
            args: Command arguments.
            source_start_idx: Index where source files start in args.
            file_path: CMakeLists.txt path.
            shared_graph: Graph manager (unused, kept for interface compatibility).
            variable_resolver: Variable resolver.
        """
        source_files_raw = extract_dependencies_from_args(args, skip_first=source_start_idx)

        source_files = []
        for source_file in source_files_raw:
            if source_file.startswith("${") and source_file.endswith("}"):
                var_name = source_file[2:-1]
                list_items = variable_resolver.expand_list_variable(var_name)
                if list_items:
                    for item in list_items:
                        resolved_item = variable_resolver.resolve(item)
                        source_files.append(resolved_item)
                else:
                    resolved = variable_resolver.resolve(source_file)
                    if resolved != source_file:
                        source_files.append(resolved)
            else:
                source_files.append(source_file)

        cmake_dir = file_path.parent

        # Collect source file data for event
        source_file_data = []
        for source_file in source_files:
            resolved_source = variable_resolver.resolve(source_file)

            if resolved_source.startswith("/"):
                source_path = Path(resolved_source)
            else:
                source_path = cmake_dir / resolved_source

            try:
                if source_path.exists():
                    source_id = normalize_node_id(source_path, self.repo_root)
                else:
                    rel_source = cmake_dir.relative_to(Path(self.repo_root)) / resolved_source
                    source_id = f"//{rel_source.as_posix()}"

                if "${" in source_id:
                    for var_match in CMAKE_VAR_PATTERN.finditer(source_id):
                        var_expr = var_match.group(0)
                        resolved_var = variable_resolver.resolve(var_expr)
                        source_id = source_id.replace(var_expr, resolved_var)

            except (ValueError, OSError):
                source_id = f"//{cmake_dir.name}/{resolved_source}"
                if "${" in source_id:
                    for var_match in CMAKE_VAR_PATTERN.finditer(source_id):
                        var_expr = var_match.group(0)
                        resolved_var = variable_resolver.resolve(var_expr)
                        source_id = source_id.replace(var_expr, resolved_var)

            source_file_data.append({"source_id": source_id, "node_type": "code"})

        if source_file_data:
            event = Event(
                event_type=EventType.CMAKE_SOURCE_FILES_ADDED,
                source=self.parser_name,
                data={
                    "target_id": target_id,
                    "source_files": source_file_data,
                    "confidence": 1.0,
                },
            )
            self.eventbus.publish(event)
            log.debug("Published CMAKE_SOURCE_FILES_ADDED for %s (%d files)", target_id, len(source_file_data))
