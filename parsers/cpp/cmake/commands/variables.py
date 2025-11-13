"""Variable command handler for set, list, and file(GLOB) commands."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from parsers.cpp.cmake.commands.base import CommandHandler
from parsers.cpp.cmake.tokens import clean_token, is_valid_dependency
from parsers.cpp.cmake.variables import CMakeVariableResolver
from utils.graph import GraphManager

log = logging.getLogger("depanalyzer.parsers.cpp.cmake.commands.variables")


class VariablesCommandHandler(CommandHandler):
    """Handler for variable manipulation commands (set, list, file GLOB)."""

    def can_handle(self, command_name: str) -> bool:
        """Check if this handler can process the given command.

        Args:
            command_name: The lowercase command name.

        Returns:
            True if command is set, list, or file.
        """
        return command_name in {"set", "list", "file"}

    def handle(
        self,
        command_name: str,
        args: List[str],
        file_path: Path,
        shared_graph: GraphManager,
        variable_resolver: CMakeVariableResolver,
    ) -> bool:
        """Handle variable manipulation commands.

        Args:
            command_name: "set", "list", or "file".
            args: Command arguments.
            file_path: CMakeLists.txt path.
            shared_graph: Graph manager.
            variable_resolver: Variable resolver.

        Returns:
            True if the command was successfully processed.
        """
        if command_name == "set":
            return self._handle_set(args, variable_resolver)
        elif command_name == "list":
            return self._handle_list(args, variable_resolver)
        elif command_name == "file":
            return self._handle_file(args, variable_resolver)
        return False

    def _handle_set(
        self, args: List[str], variable_resolver: CMakeVariableResolver
    ) -> bool:
        """Handle set() command.

        Args:
            args: Command arguments.
            variable_resolver: Variable resolver.

        Returns:
            True if handled successfully.
        """
        if len(args) < 2:
            return False

        var_name = clean_token(args[0])
        if not var_name:
            return False

        if len(args) == 2:
            var_value = clean_token(args[1])
            if var_value:
                variable_resolver.set_variable(var_name, var_value)
        else:
            items = []
            for item in args[1:]:
                clean_item = clean_token(item)
                if clean_item and is_valid_dependency(clean_item):
                    items.append(clean_item)
            if items:
                variable_resolver.set_list(var_name, items)
        return True

    def _handle_list(
        self, args: List[str], variable_resolver: CMakeVariableResolver
    ) -> bool:
        """Handle list(APPEND ...) command.

        Args:
            args: Command arguments.
            variable_resolver: Variable resolver.

        Returns:
            True if handled successfully.
        """
        if len(args) < 3:
            return False

        list_op = args[0].upper()
        if list_op != "APPEND":
            return False

        list_name = clean_token(args[1])
        if not list_name:
            return False

        items_to_append = []
        for item in args[2:]:
            clean_item = clean_token(item)
            if clean_item and is_valid_dependency(clean_item):
                items_to_append.append(clean_item)

        if items_to_append:
            variable_resolver.append_to_list(list_name, items_to_append)

        return True

    def _handle_file(
        self, args: List[str], variable_resolver: CMakeVariableResolver
    ) -> bool:
        """Handle file(GLOB ...) command.

        Args:
            args: Command arguments.
            variable_resolver: Variable resolver.

        Returns:
            True if handled successfully.
        """
        if len(args) < 3:
            return False

        file_op = args[0].upper()
        if file_op != "GLOB":
            return False

        var_name = clean_token(args[1])
        log.info("Processing file(GLOB) command for variable: %s", var_name)

        if not var_name:
            return False

        patterns = []
        for pattern in args[2:]:
            clean_pattern = clean_token(pattern)
            if clean_pattern:
                patterns.append(clean_pattern)

        log.info("  Glob patterns: %s", patterns)

        if patterns:
            matching_files = variable_resolver.glob_files(patterns)
            log.info("  Found %d files: %s", len(matching_files), matching_files)
            variable_resolver.set_list(var_name, matching_files)

        return True
