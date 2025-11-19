"""C/CMake configuration parser building a dependency graph.

This module parses CMakeLists.txt files using tree-sitter-cmake and command handlers.
It extracts targets, dependencies, sources, and other CMake entities into a graph.
"""

# Parser must keep running even if individual files are malformed.
# pylint: disable=broad-exception-caught

from __future__ import annotations

import re
import logging
from pathlib import Path
from typing import Any, List

from depanalyzer.parsers.base import BaseParser
from depanalyzer.graph.manager import GraphManager
from depanalyzer.runtime.eventbus import Event, EventType

from depanalyzer.parsers.cpp.cmake.grammar import CMAKE_PARSER, ParseTokenWrapper
from depanalyzer.parsers.cpp.cmake import tokens
from depanalyzer.parsers.cpp.cmake.variables import CMakeVariableResolver
from depanalyzer.parsers.cpp.cmake.commands import (
    TargetCommandHandler,
    LinkingCommandHandler,
    VariablesCommandHandler,
    PackagesCommandHandler,
    IncludesCommandHandler,
    SourcesCommandHandler,
)
from depanalyzer.parsers.cpp.cmake_graph_builder import CMakeGraphBuilder

log = logging.getLogger("depanalyzer.parsers.cpp.config_parser")


class CMakeParser(BaseParser):
    """Parser for CMake files that builds and enriches a dependency graph.

    Uses tree-sitter-cmake parser. Delegates command processing to specialized
    command handlers following the Command pattern for better separation of concerns.

    Uses event-driven architecture: command handlers publish events, and the
    CMakeGraphBuilder hook subscribes to those events to build the graph.
    """

    NAME = "cmake"

    def __init__(
        self,
        workspace_root: Path,
        graph_manager: GraphManager,
        eventbus,
        config: Any | None = None,
    ) -> None:
        """Initialize the CMake config parser with command handlers and graph builder hook.

        Args:
            workspace_root: Workspace root directory path.
            graph_manager: Transaction graph manager.
            eventbus: Event bus for publishing parse events.
        """
        super().__init__(workspace_root, graph_manager, eventbus, config=config)

        # Initialize the CMakeGraphBuilder hook that consumes CMake events
        # and performs graph operations
        self.cmake_graph_builder = CMakeGraphBuilder(graph_manager, eventbus)
        log.info("CMakeGraphBuilder hook initialized")

        self._init_command_handlers()
        self._last_parsed_dir: Path | None = None

    def _init_command_handlers(self) -> None:
        """Initialize all command handlers."""
        # Command handlers now receive eventbus for event-driven architecture
        # They publish events instead of directly manipulating the graph
        # Use graph_manager.root_path (scan target root) instead of workspace_root (tool directory)
        repo_root_str = str(self.graph_manager.root_path)
        self.handlers = [
            TargetCommandHandler(repo_root_str, self.NAME, self.eventbus),
            LinkingCommandHandler(repo_root_str, self.NAME, self.eventbus),
            IncludesCommandHandler(repo_root_str, self.NAME, self.eventbus),
            SourcesCommandHandler(repo_root_str, self.NAME, self.eventbus),
            VariablesCommandHandler(repo_root_str, self.NAME, self.eventbus),
            PackagesCommandHandler(repo_root_str, self.NAME, self.eventbus),
        ]

    def parse(self, target_path: Path) -> None:
        """Parse a CMake file and update the graph.

        Extracts targets, dependencies, link relationships, and other CMake
        constructs using tree-sitter parsing. Files that fail to parse are
        skipped with a warning.

        Args:
            target_path: Path to the CMakeLists.txt or .cmake file.
        """
        try:
            text = target_path.read_text(encoding="utf8", errors="ignore")
        except OSError as e:
            log.warning("Failed to read %s: %s", target_path, e)
            return

        self._last_parsed_dir = target_path.parent.resolve()

        variable_resolver = CMakeVariableResolver(str(self.graph_manager.root_path), target_path)

        try:
            tree = CMAKE_PARSER.parse(text)
            self._process_parse_tree(tree, self.graph_manager, variable_resolver, target_path)
        except Exception as e:
            log.warning("Failed to parse %s: %s - skipping file", target_path, e)
            return

        # Publish parse completion event
        event = Event(
            event_type=EventType.CMAKE_TARGET_COMPLETED,
            source=self.NAME,
            data={
                "target_path": str(target_path),
                "file_name": target_path.name,
            },
        )
        self.publish_parse_event(event)

    def discover_code_files(self) -> List[Path]:
        """Discover C/C++ source and header files for the last parsed CMake target.

        Args:
            None.

        Returns:
            List[Path]: List of source code files to feed into code parsing.
        """
        if self._last_parsed_dir is None:
            return []

        code_files: List[Path] = []
        try:
            patterns = [
                "*.c",
                "*.cpp",
                "*.cc",
                "*.cxx",
                "*.c++",
                "*.h",
                "*.hpp",
                "*.hh",
                "*.hxx",
                "*.h++",
            ]
            for pattern in patterns:
                for file_path in self._last_parsed_dir.rglob(pattern):
                    if file_path.is_file():
                        code_files.append(file_path)
        except (OSError, ValueError) as exc:
            log.warning(
                "Failed to discover C/C++ code files under %s: %s",
                self._last_parsed_dir,
                exc,
            )
            return []

        unique_files: List[Path] = []
        seen: set[Path] = set()
        for path in code_files:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            unique_files.append(resolved)

        return unique_files

    def _process_parse_tree(
        self,
        tree,
        shared_graph: GraphManager,
        variable_resolver: CMakeVariableResolver,
        file_path: Path,
    ) -> None:
        """Process tree-sitter parse tree using command handlers.

        Args:
            tree: Tree-sitter parse tree wrapper.
            shared_graph: Graph manager.
            variable_resolver: Variable resolver.
            file_path: CMakeLists.txt path.
        """
        for cmd in tree.find_data("command_invocation"):
            if not cmd.children:
                continue

            ident = cmd.children[0]
            if not isinstance(ident, ParseTokenWrapper):
                continue

            cmd_name = str(ident).lower()

            cmd_text = (
                "".join([str(c) for c in cmd.children[1:]])
                if len(cmd.children) > 1
                else ""
            )
            m = re.search(r"\((.*)\)\s*$", cmd_text, flags=re.DOTALL)
            arg_text = m.group(1) if m else ""

            args = tokens.extract_arg_tokens(arg_text)

            self._handle_command(
                cmd_name, args, file_path, shared_graph, variable_resolver
            )

    def _handle_command(
        self,
        cmd_name: str,
        args: List[str],
        file_path: Path,
        shared_graph: GraphManager,
        variable_resolver: CMakeVariableResolver,
    ) -> None:
        """Delegate command processing to appropriate handler.

        Args:
            cmd_name: Lowercase command name.
            args: Tokenized arguments.
            file_path: CMakeLists.txt path.
            shared_graph: Graph manager.
            variable_resolver: Variable resolver.
        """
        # Special handling for project()
        if cmd_name == "project" and args:
            project_name = tokens.clean_token(args[0])
            if project_name:
                variable_resolver.set_project_name(project_name)
            return

        # Special handling for add_subdirectory()
        if cmd_name == "add_subdirectory" and args:
            sub = tokens.clean_token(args[0])
            if sub and tokens.is_valid_dependency(sub):
                resolved_sub = variable_resolver.resolve(sub)
                sub_path = Path(self.graph_manager.root_path) / resolved_sub
                if sub_path.is_dir():
                    dir_id = f"//{resolved_sub}"
                    # Use new graph API
                    self.add_node(
                        node_id=dir_id,
                        node_type="subdirectory",
                        parser_name=self.NAME,
                        src_path=str(sub_path.resolve()),
                        id=dir_id,
                    )
            return

        # Delegate to command handlers
        for handler in self.handlers:
            if handler.can_handle(cmd_name):
                handler.handle(
                    cmd_name, args, file_path, shared_graph, variable_resolver
                )
                return
