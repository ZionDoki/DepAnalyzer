"""Base command handler for CMake commands.

Defines the abstract interface that all command handlers must implement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List

from depanalyzer.graph import GraphManager
from depanalyzer.parsers.cpp.cmake.variables import CMakeVariableResolver
from depanalyzer.runtime.eventbus import EventBus


class CommandHandler(ABC):
    """Abstract base class for CMake command handlers.

    Each command handler is responsible for processing a specific set of
    related CMake commands (e.g., add_library/add_executable, target_link_libraries, etc.).

    Handlers extract facts from CMake commands and publish events for hooks to consume.
    Direct graph manipulation should be avoided in favor of event-driven architecture.
    """

    def __init__(self, repo_root: str, parser_name: str, eventbus: EventBus):
        """Initialize the command handler.

        Args:
            repo_root: Repository root directory path.
            parser_name: Name of the parser (e.g., "cmake").
            eventbus: Event bus for publishing command events.
        """
        self.repo_root = repo_root
        self.parser_name = parser_name
        self.eventbus = eventbus

    @abstractmethod
    def handle(
        self,
        command_name: str,
        args: List[str],
        file_path: Path,
        shared_graph: GraphManager,
        variable_resolver: CMakeVariableResolver,
    ) -> bool:
        """Handle a CMake command.

        Args:
            command_name: The lowercase command name (e.g., "add_library").
            args: List of tokenized command arguments.
            file_path: Path to the CMakeLists.txt file containing this command.
            shared_graph: Graph manager for adding nodes and edges.
            variable_resolver: Variable resolver for expanding CMake variables.

        Returns:
            True if the command was handled, False otherwise.
        """
        raise NotImplementedError

    @abstractmethod
    def can_handle(self, command_name: str) -> bool:
        """Check if this handler can process the given command.

        Args:
            command_name: The lowercase command name.

        Returns:
            True if this handler can process the command, False otherwise.
        """
        raise NotImplementedError
