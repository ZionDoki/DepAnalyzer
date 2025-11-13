"""Files command handler for file operations."""

from __future__ import annotations

from parsers.cpp.cmake.commands.base import CommandHandler


class FilesCommandHandler(CommandHandler):
    """Handler for file commands.

    Note: file(GLOB) is handled by VariablesCommandHandler.
    This is a placeholder for other file operations if needed.
    """

    def can_handle(self, command_name: str) -> bool:
        """Check if this handler can process the given command."""
        return False

    def handle(self, command_name, args, file_path, shared_graph, variable_resolver):
        """Handle file commands.

        This is a placeholder.
        """
        return False
