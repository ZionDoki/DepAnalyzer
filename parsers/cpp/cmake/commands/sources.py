"""Sources command handler for target_sources command.

Resolves additional sources added via target_sources and attaches them to the
target using 'sources' edges. Supports basic scope keywords and variable/list
expansion for conservative, compliance-safe mapping.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from base.base import normalize_node_id
from parsers.cpp.cmake.commands.base import CommandHandler
from parsers.cpp.cmake.tokens import clean_token, CMAKE_VAR_PATTERN
from parsers.cpp.cmake.variables import CMakeVariableResolver
from utils.graph import GraphManager


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
        """Handle target_sources command.

        Adds edges from the target to resolved source files. When variables
        expand to lists, every item is attached. Unknown tokens are ignored.
        """
        if not args:
            return False

        target_tok = clean_token(args[0])
        if not target_tok:
            return False
        target_id = normalize_node_id(file_path, self.repo_root, variable_resolver.resolve(target_tok))

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
                    repo = Path(self.repo_root).resolve()
                    pr = p.resolve()
                    if pr.is_relative_to(repo):
                        source_id = f"//{pr.relative_to(repo).as_posix()}"
                    else:
                        source_id = f"//external:{pr.as_posix()}"
                except (OSError, RuntimeError, ValueError):
                    # Fall back to a repo-relative label if possible
                    try:
                        rel = (cmake_dir / resolved).resolve().relative_to(Path(self.repo_root).resolve())
                        source_id = f"//{rel.as_posix()}"
                    except Exception:
                        source_id = f"//{resolved}"

                # Resolve nested variable patterns inside id
                if "${" in source_id:
                    for m in CMAKE_VAR_PATTERN.finditer(source_id):
                        var_expr = m.group(0)
                        source_id = source_id.replace(var_expr, variable_resolver.resolve(var_expr))

                src_v = shared_graph.create_vertex(source_id, parser_name=self.parser_name, type="code", id=source_id)
                shared_graph.add_node(src_v)
                e = shared_graph.create_edge(
                    target_id,
                    source_id,
                    parser_name=self.parser_name,
                    label="sources",
                    kind="sources",
                    source_scope=None,
                )
                shared_graph.add_edge(e)
        return True
