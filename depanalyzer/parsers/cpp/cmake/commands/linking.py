"""Linking command handler for target_link_libraries command."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from depanalyzer.graph import normalize_node_id
from depanalyzer.parsers.cpp.cmake.commands.base import CommandHandler
from depanalyzer.parsers.cpp.cmake.tokens import clean_token, CMAKE_VAR_PATTERN
from depanalyzer.parsers.cpp.cmake.variables import CMakeVariableResolver
from depanalyzer.graph import GraphManager
from depanalyzer.runtime.eventbus import Event, EventType

log = logging.getLogger("depanalyzer.parsers.cpp.cmake.commands.linking")


class LinkingCommandHandler(CommandHandler):
    """Handler for linking commands (target_link_libraries)."""

    def can_handle(self, command_name: str) -> bool:
        """Check if this handler can process the given command."""
        return command_name == "target_link_libraries"

    def handle(
        self,
        command_name: str,
        args: List[str],
        file_path: Path,
        shared_graph: GraphManager,
        variable_resolver: CMakeVariableResolver,
    ) -> bool:
        """Handle target_link_libraries command by publishing CMAKE_LINK_DEPENDENCY_FOUND events.

        Args:
            command_name: The command name.
            args: Command arguments.
            file_path: CMakeLists.txt path.
            shared_graph: Graph manager (unused, kept for interface compatibility).
            variable_resolver: Variable resolver.

        Returns:
            True if handled successfully.
        """
        if len(args) < 2:
            return False

        target = clean_token(args[0])
        if not target:
            return False

        target = variable_resolver.resolve(target)
        source_id = normalize_node_id(file_path, self.repo_root, target)

        # Collect all link dependencies
        link_dependencies = []

        # Iterate arguments
        i = 1
        while i < len(args):
            raw = args[i]
            tok = clean_token(raw)
            if not tok:
                i += 1
                continue
            up = tok.upper()
            # Skip scope keywords (not needed, node types are sufficient)
            if up in {"PUBLIC", "PRIVATE", "INTERFACE"}:
                i += 1
                continue
            # Skip generator expressions and configuration keywords
            if tok.startswith("$<") and tok.endswith(">"):
                ge_body = tok[2:-1]
                # Handle $<TARGET_PROPERTY:target,prop> and $<CONFIG:...> by best-effort passthrough
                if ge_body.startswith("TARGET_PROPERTY:"):
                    parts = ge_body.split(":", 1)[1].split(",", 1)
                    if parts:
                        tok = parts[0]
                        up = tok.upper()
                    else:
                        i += 1
                        continue
                else:
                    # Skip unknown generator expressions
                    i += 1
                    continue
            if up in {"DEBUG", "OPTIMIZED", "GENERAL", "CHECK_BASE_ADDR", "DEFAULT"}:
                i += 1
                # Skip next token if it is the paired library for these keywords
                if i < len(args):
                    i += 1
                continue

            lib = tok
            resolved_lib = variable_resolver.resolve(lib)

            if resolved_lib.startswith("$<") and resolved_lib.endswith(">"):
                target_id = f"//external:{resolved_lib}"
            elif resolved_lib.startswith("//"):
                target_id = resolved_lib
            elif "::" in resolved_lib:
                target_id = f"//{resolved_lib}"
            elif CMAKE_VAR_PATTERN.search(lib):
                # Unresolved variable: model as external placeholder conservatively
                target_id = f"//external:{lib}"
            else:
                if resolved_lib.endswith((".so", ".lib", ".dll", ".a")):
                    target_id = f"//system:{resolved_lib}"
                else:
                    target_id = normalize_node_id(file_path, self.repo_root, resolved_lib)

            link_dependencies.append({
                "target_id": target_id,
                "library_name": resolved_lib,
            })
            i += 1

        # Publish event with all link dependencies
        if link_dependencies:
            event = Event(
                event_type=EventType.CMAKE_LINK_DEPENDENCY_FOUND,
                source=self.parser_name,
                data={
                    "source_id": source_id,
                    "link_dependencies": link_dependencies,
                    "confidence": 1.0,
                    "over_approx": False,
                },
            )
            self.eventbus.publish(event)
            log.debug("Published CMAKE_LINK_DEPENDENCY_FOUND for %s (%d deps)", source_id, len(link_dependencies))

        return True
