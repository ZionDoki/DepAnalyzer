"""Includes command handler for target include directories.

Captures include search paths from target_include_directories and stores them
on the target node as an "include_dirs" attribute. These paths are later used
by projection logic to resolve //include:* placeholders conservatively.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from parsers.cpp.cmake.commands.base import CommandHandler
from parsers.cpp.cmake.tokens import clean_token
from parsers.cpp.cmake.variables import CMakeVariableResolver
from graph.manager import GraphManager
from core.identifiers import normalize_node_id

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
        """Handle target_include_directories command.

        Records include directories on the target node to improve later
        include placeholder resolution. Scope keywords are ignored; paths are
        resolved relative to the current CMake directory when possible.
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

        # Merge with existing include_dirs on the node
        node_data = shared_graph.get_node(target_id)
        current = node_data.get("include_dirs") if node_data else None
        merged: list[str] = []
        seen: set[str] = set()
        if isinstance(current, list):
            for d in current:
                if isinstance(d, str) and d not in seen:
                    merged.append(d)
                    seen.add(d)
        for d in paths:
            if d not in seen:
                merged.append(d)
                seen.add(d)

        # Ensure node exists and update attribute
        if not shared_graph.has_node(target_id):
            shared_graph.add_node(
                target_id, node_type="artifact", parser_name=self.parser_name, id=target_id
            )
        shared_graph._backend.native_graph.nodes[target_id]["include_dirs"] = merged
        log.debug("Recorded %s include dirs on %s", len(paths), target_id)
        return True
