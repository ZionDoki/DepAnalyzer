"""Linking command handler for target_link_libraries command."""

from __future__ import annotations

from pathlib import Path
from typing import List

from base.base import normalize_node_id
from parsers.cpp.cmake.commands.base import CommandHandler
from parsers.cpp.cmake.tokens import clean_token, CMAKE_VAR_PATTERN
from parsers.cpp.cmake.variables import CMakeVariableResolver
from utils.graph import GraphManager


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
        """Handle target_link_libraries command.

        Emits link_libraries edges between targets.
        """
        if len(args) < 2:
            return False

        target = clean_token(args[0])
        if not target:
            return False

        target = variable_resolver.resolve(target)
        source_id = normalize_node_id(file_path, self.repo_root, target)

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
                i += 1
                continue
            if up in {"DEBUG", "OPTIMIZED", "GENERAL"}:
                i += 1
                # Skip next token if it is the paired library for these keywords
                if i < len(args):
                    i += 1
                continue

            lib = tok
            resolved_lib = variable_resolver.resolve(lib)

            if resolved_lib.startswith("//"):
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

            e = shared_graph.create_edge(
                source_id,
                target_id,
                parser_name=self.parser_name,
                label="link_libraries",
                kind="link_libraries",
            )
            shared_graph.add_edge(e)
            i += 1

        return True
