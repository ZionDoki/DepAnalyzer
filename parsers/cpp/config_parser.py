"""C/CMake configuration parser building a dependency graph.

This module parses CMakeLists.txt files using a Lark grammar and command handlers.
It extracts targets, dependencies, sources, and other CMake entities into a graph.
"""

from __future__ import annotations

import re
import logging
from pathlib import Path
from typing import List

from lark import Token
from lark.exceptions import LarkError

from parsers.base import BaseParser
from graph.manager import GraphManager
from runtime.eventbus import Event, EventType

from parsers.cpp.cmake.grammar import CMAKE_PARSER
from parsers.cpp.cmake import tokens, handlers
from parsers.cpp.cmake.variables import CMakeVariableResolver
from parsers.cpp.cmake.commands import (
    TargetCommandHandler,
    LinkingCommandHandler,
    VariablesCommandHandler,
    PackagesCommandHandler,
    IncludesCommandHandler,
    SourcesCommandHandler,
)

log = logging.getLogger("depanalyzer.parsers.cpp.config_parser")


class CMakeParser(BaseParser):
    """Parser for CMake files that builds and enriches a dependency graph.

    Uses a Lark grammar parser with regex fallback for robustness. Delegates
    command processing to specialized command handlers following the Command
    pattern for better separation of concerns.
    """

    NAME = "cmake"

    def __init__(self, workspace_root: Path, graph_manager: GraphManager, eventbus) -> None:
        """Initialize the CMake config parser with command handlers.

        Args:
            workspace_root: Workspace root directory path.
            graph_manager: Transaction graph manager.
            eventbus: Event bus for publishing parse events.
        """
        super().__init__(workspace_root, graph_manager, eventbus)
        self._init_command_handlers()

    def _init_command_handlers(self) -> None:
        """Initialize all command handlers."""
        # Command handlers still use repo_root internally - pass workspace_root
        # They will be updated gradually as needed
        repo_root_str = str(self.workspace_root)
        self.handlers = [
            TargetCommandHandler(repo_root_str, self.NAME),
            LinkingCommandHandler(repo_root_str, self.NAME),
            IncludesCommandHandler(repo_root_str, self.NAME),
            SourcesCommandHandler(repo_root_str, self.NAME),
            VariablesCommandHandler(repo_root_str, self.NAME),
            PackagesCommandHandler(repo_root_str, self.NAME),
        ]

    def parse(self, target_path: Path) -> None:
        """Parse a CMake file and update the graph.

        Extracts targets, dependencies, link relationships, and other CMake
        constructs using either Lark tree parsing or regex fallback.

        Args:
            target_path: Path to the CMakeLists.txt or .cmake file.
        """
        try:
            text = target_path.read_text(encoding="utf8", errors="ignore")
        except OSError as e:
            log.warning("Failed to read %s: %s", target_path, e)
            return

        variable_resolver = CMakeVariableResolver(str(self.workspace_root), target_path)

        try:
            tree = CMAKE_PARSER.parse(text)
            self._process_parse_tree(tree, self.graph_manager, variable_resolver, target_path)
        except LarkError as e:
            log.debug("Lark parsing failed for %s: %s", target_path, e)
            self._fallback_regex_parse(text, self.graph_manager, variable_resolver, target_path)

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

    def _process_parse_tree(
        self,
        tree,
        shared_graph: GraphManager,
        variable_resolver: CMakeVariableResolver,
        file_path: Path,
    ) -> None:
        """Process Lark parse tree using command handlers.

        Args:
            tree: Lark parse tree.
            shared_graph: Graph manager.
            variable_resolver: Variable resolver.
            file_path: CMakeLists.txt path.
        """
        for cmd in tree.find_data("command_invocation"):
            if not cmd.children:
                continue

            ident = cmd.children[0]
            if not isinstance(ident, Token):
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
                sub_path = Path(self.workspace_root) / resolved_sub
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

    def _fallback_regex_parse(
        self,
        text: str,
        shared_graph: GraphManager,
        variable_resolver: CMakeVariableResolver,
        file_path: Path,
    ) -> None:
        """Fallback regex parser when Lark parsing fails.

        Extracts common CMake commands using regex patterns and delegates
        to the same command handlers for processing.

        Args:
            text: Raw CMake file text.
            shared_graph: Graph manager.
            variable_resolver: Variable resolver.
            file_path: CMakeLists.txt path.
        """
        # Extract project name
        project_re = re.compile(
            r"project\s*\(\s*([A-Za-z0-9_${}\-.:]+)", flags=re.IGNORECASE
        )
        for m in project_re.finditer(text):
            project_name = tokens.clean_token(m.group(1))
            if project_name:
                variable_resolver.set_project_name(project_name)
                break

        # Process common commands using regex
        command_patterns = {
            "set": r"set\s*\(\s*([A-Za-z0-9_${}\-.:]+)([^)]+)\)",
            "list": r"list\s*\(\s*APPEND\s+([A-Za-z0-9_${}\-.:]+)([^)]+)\)",
            "file": r"file\s*\(\s*GLOB\s+([A-Za-z0-9_${}\-.:]+)([^)]+)\)",
        }

        for cmd_name, pattern in command_patterns.items():
            for m in re.finditer(pattern, text, re.IGNORECASE | re.DOTALL):
                var_name = tokens.clean_token(m.group(1))
                args_text = m.group(2) if len(m.groups()) >= 2 else ""
                if var_name:
                    if cmd_name == "set":
                        items = tokens.extract_arg_tokens(args_text)
                        self._handle_command(
                            cmd_name,
                            [var_name] + items,
                            file_path,
                            shared_graph,
                            variable_resolver,
                        )
                    elif cmd_name == "list":
                        items = tokens.extract_arg_tokens(args_text)
                        self._handle_command(
                            cmd_name,
                            ["APPEND", var_name] + items,
                            file_path,
                            shared_graph,
                            variable_resolver,
                        )
                    elif cmd_name == "file":
                        patterns = tokens.extract_arg_tokens(args_text)
                        self._handle_command(
                            cmd_name,
                            ["GLOB", var_name] + patterns,
                            file_path,
                            shared_graph,
                            variable_resolver,
                        )

        # Process target commands (add_library, add_executable)
        self._fallback_process_targets(text, shared_graph, variable_resolver, file_path)

        # Process linking commands
        self._fallback_process_linking(text, shared_graph, variable_resolver, file_path)

        # Process package commands
        self._fallback_process_packages(
            text, shared_graph, variable_resolver, file_path
        )

    def _fallback_process_targets(
        self,
        text: str,
        shared_graph: GraphManager,
        variable_resolver: CMakeVariableResolver,
        file_path: Path,
    ) -> None:
        """Process target commands in fallback mode.

        Args:
            text: CMake file text.
            shared_graph: Graph manager.
            variable_resolver: Variable resolver.
            file_path: CMakeLists.txt path.
        """
        add_lib_cmds = handlers.find_cmake_commands(r"add_library\s*\(", text)
        add_exe_cmds = handlers.find_cmake_commands(r"add_executable\s*\(", text)

        for target_name, args_text in add_lib_cmds + add_exe_cmds:
            target = tokens.clean_token(target_name)
            if not target:
                continue

            args_list = tokens.extract_arg_tokens(args_text)
            cmd_name = (
                "add_library"
                if (target_name, args_text) in add_lib_cmds
                else "add_executable"
            )

            self._handle_command(
                cmd_name,
                [target] + args_list,
                file_path,
                shared_graph,
                variable_resolver,
            )

    def _fallback_process_linking(
        self,
        text: str,
        shared_graph: GraphManager,
        variable_resolver: CMakeVariableResolver,
        file_path: Path,
    ) -> None:
        """Process linking commands in fallback mode.

        Args:
            text: CMake file text.
            shared_graph: Graph manager.
            variable_resolver: Variable resolver.
            file_path: CMakeLists.txt path.
        """
        tlink_re = re.compile(
            r"target_link_libraries\s*\(\s*([A-Za-z0-9_${}\-.:]+)\s+([^\)]+)\)",
            flags=re.IGNORECASE | re.DOTALL,
        )

        for m in tlink_re.finditer(text):
            target = tokens.clean_token(m.group(1))
            if not target:
                continue

            libs_text = m.group(2).strip()
            cleaned_lines = []
            for line in libs_text.split("\n"):
                comment_pos = line.find("#")
                if comment_pos >= 0:
                    line = line[:comment_pos]
                line = line.strip()
                if line:
                    cleaned_lines.append(line)

            cleaned_libs_text = " ".join(cleaned_lines)
            raw_libs = re.findall(r'"[^"]*"|\'[^\']*\'|\S+', cleaned_libs_text)

            self._handle_command(
                "target_link_libraries",
                [target] + raw_libs,
                file_path,
                shared_graph,
                variable_resolver,
            )

    def _fallback_process_packages(
        self,
        text: str,
        shared_graph: GraphManager,
        variable_resolver: CMakeVariableResolver,
        file_path: Path,
    ) -> None:
        """Process package commands in fallback mode.

        Args:
            text: CMake file text.
            shared_graph: Graph manager.
            variable_resolver: Variable resolver.
            file_path: CMakeLists.txt path.
        """
        # FetchContent_Declare
        fetchcontent_pattern = re.compile(
            r"FetchContent_Declare\s*\(", flags=re.IGNORECASE
        )
        for match in fetchcontent_pattern.finditer(text):
            paren_pos = match.end() - 1
            full_args = handlers.extract_balanced_parentheses(text, paren_pos)
            if full_args:
                args_tokens = tokens.extract_arg_tokens(full_args)
                if args_tokens:
                    self._handle_command(
                        "fetchcontent_declare",
                        args_tokens,
                        file_path,
                        shared_graph,
                        variable_resolver,
                    )

        # find_package
        fp_pattern = re.compile(r"find_package\s*\(", flags=re.IGNORECASE)
        for m in fp_pattern.finditer(text):
            paren_pos = m.end() - 1
            full_args = handlers.extract_balanced_parentheses(text, paren_pos)
            if full_args:
                toks = tokens.extract_arg_tokens(full_args)
                if toks:
                    self._handle_command(
                        "find_package",
                        toks,
                        file_path,
                        shared_graph,
                        variable_resolver,
                    )
