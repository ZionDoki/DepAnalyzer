"""Includes command handler for target include directories.

Captures include search paths from target_include_directories and stores them
on the target node as an "include_dirs" attribute. These paths are later used
by projection logic to resolve //include:* placeholders conservatively.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from depanalyzer.parsers.cpp.cmake.commands.base import CommandHandler
from depanalyzer.parsers.cpp.cmake.tokens import clean_token
from depanalyzer.parsers.cpp.cmake.variables import CMakeVariableResolver
from depanalyzer.graph.manager import GraphManager
from depanalyzer.core.identifiers import normalize_node_id
from depanalyzer.runtime.eventbus import Event, EventType

log = logging.getLogger("depanalyzer.parsers.cpp.cmake.commands.includes")


class IncludesCommandHandler(CommandHandler):
    """Handler for include directory commands."""

    def can_handle(self, command_name: str) -> bool:
        """Check if this handler can process the given command."""
        return command_name == "target_include_directories"

    def handle(
        self,
        command_name: str,
        args: List[str],
        file_path: Path,
        shared_graph: GraphManager,
        variable_resolver: CMakeVariableResolver,
    ) -> bool:
        """Handle target_include_directories command by publishing CMAKE_INCLUDE_DIRS_DECLARED event.

        Args:
            command_name: The command name.
            args: Command arguments.
            file_path: CMakeLists.txt path.
            shared_graph: Graph manager for checking existing includes.
            variable_resolver: Variable resolver.

        Returns:
            True if handled successfully.
        """
        if not args:
            return False

        target_tok = clean_token(args[0])
        if not target_tok:
            return False

        target_name = variable_resolver.resolve(target_tok)
        target_id = normalize_node_id(file_path, self.repo_root, target_name)

        paths: list[str] = []
        for raw in args[1:]:
            tok = clean_token(raw)
            if not tok:
                continue
            up = tok.upper()
            if up in {"PUBLIC", "PRIVATE", "INTERFACE", "SYSTEM"}:
                continue
            # Skip generator expressions; too context-sensitive for now
            if tok.startswith("$<") and tok.endswith(">"):
                continue
            resolved = variable_resolver.resolve(tok)
            p = Path(resolved)
            if not p.is_absolute():
                p = Path(file_path).parent / resolved
            try:
                # Store repo-relative if within repo, else normalized posix string
                rel = p.resolve()
                repo = Path(self.repo_root).resolve()
                if rel.is_relative_to(repo):
                    rel_str = rel.relative_to(repo).as_posix()
                    paths.append(rel_str)
                else:
                    paths.append(rel.as_posix().lstrip("/"))
            except (OSError, RuntimeError, ValueError):
                paths.append(p.as_posix().lstrip("/"))

        if not paths:
            return False

        # Get existing include_dirs if target already exists
        node_data = shared_graph.get_node(target_id)
        current = node_data.get("include_dirs") if node_data else None

        # Publish event with new include directories
        event = Event(
            event_type=EventType.CMAKE_INCLUDE_DIRS_DECLARED,
            source=self.parser_name,
            data={
                "target_id": target_id,
                "include_dirs": paths,
                "existing_include_dirs": current if isinstance(current, list) else [],
                "confidence": 1.0,
            },
        )
        self.eventbus.publish(event)
        log.debug("Published CMAKE_INCLUDE_DIRS_DECLARED for %s (%d dirs)", target_id, len(paths))

        return True
