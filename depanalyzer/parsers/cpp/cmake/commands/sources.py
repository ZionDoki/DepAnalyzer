"""Sources command handler for target_sources command.

Resolves additional sources added via target_sources and attaches them to the
target using 'sources' edges. Supports basic scope keywords and variable/list
expansion for conservative, compliance-safe mapping.
"""

# Command handlers keep running even if individual source items are malformed.


from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from depanalyzer.graph.identifiers import normalize_node_id
from depanalyzer.parsers.cpp.cmake.commands.base import CommandHandler
from depanalyzer.parsers.cpp.cmake.tokens import clean_token, CMAKE_VAR_PATTERN
from depanalyzer.parsers.cpp.cmake.variables import CMakeVariableResolver
from depanalyzer.graph.manager import GraphManager
from depanalyzer.runtime.eventbus import Event, EventType

log = logging.getLogger("depanalyzer.parsers.cpp.cmake.commands.sources")


class SourcesCommandHandler(CommandHandler):
    """Handler for source manipulation commands (target_sources)."""

    def can_handle(self, command_name: str) -> bool:
        """Check if this handler can process the given command."""
        return command_name == "target_sources"

    def handle(
        self,
        command_name: str,
        args: List[str],
        file_path: Path,
        shared_graph: GraphManager,
        variable_resolver: CMakeVariableResolver,
    ) -> bool:
        """Handle target_sources command by publishing CMAKE_SOURCE_FILES_ADDED event.

        Args:
            command_name: The command name.
            args: Command arguments.
            file_path: CMakeLists.txt path.
            shared_graph: Graph manager (unused, kept for interface compatibility).
            variable_resolver: Variable resolver.

        Returns:
            True if handled successfully.
        """
        if not args:
            return False

        target_tok = clean_token(args[0])
        if not target_tok:
            return False
        target_id = normalize_node_id(
            file_path, self.repo_root, variable_resolver.resolve(target_tok)
        )

        # Collect candidate source tokens, skipping scope keywords
        candidates: list[str] = []
        scope_keywords = {"PRIVATE", "PUBLIC", "INTERFACE"}
        for raw in args[1:]:
            tok = clean_token(raw)
            if not tok:
                continue
            if tok.upper() in scope_keywords:
                continue
            if tok.startswith("$<") and tok.endswith(">"):
                continue
            candidates.append(tok)

        cmake_dir = file_path.parent
        source_file_data = []

        for token in candidates:
            items: list[str] = []
            if token.startswith("${") and token.endswith("}"):
                var_name = token[2:-1]
                lst = variable_resolver.expand_list_variable(var_name)
                if lst:
                    items.extend(lst)
                else:
                    items.append(variable_resolver.resolve(token))
            else:
                items.append(token)

            for item in items:
                resolved = variable_resolver.resolve(item)
                p = Path(resolved)
                if not p.is_absolute():
                    p = cmake_dir / resolved
                # Compute a stable node id even when file does not yet exist
                try:
                    # Use normalize_node_id for consistent path handling
                    pr = p.resolve()
                    source_id = normalize_node_id(pr, self.repo_root)
                except (OSError, RuntimeError, ValueError):
                    # Fall back to a repo-relative label if possible
                    try:
                        rel = (
                            (cmake_dir / resolved)
                            .resolve()
                            .relative_to(Path(self.repo_root).resolve())
                        )
                        source_id = f"//{rel.as_posix()}"
                    except Exception:
                        source_id = f"//{resolved}"

                # Resolve nested variable patterns inside id
                if "${" in source_id:
                    for m in CMAKE_VAR_PATTERN.finditer(source_id):
                        var_expr = m.group(0)
                        source_id = source_id.replace(
                            var_expr, variable_resolver.resolve(var_expr)
                        )

                source_file_data.append({"source_id": source_id, "node_type": "code"})

        # Publish event with all source files
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
            log.debug(
                "Published CMAKE_SOURCE_FILES_ADDED for %s (%d files)",
                target_id,
                len(source_file_data),
            )

        return True
