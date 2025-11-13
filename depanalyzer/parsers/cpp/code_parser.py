"""C/C++ code parser extracting include relationships.

This module parses C/C++ source files to extract include relationships and
records them in the shared dependency graph. It can use tree-sitter when
available and falls back to a regex-based approach otherwise.
"""

# depanalyzer/parsers/c/code_parser.py
from __future__ import annotations
import logging
import re
from pathlib import Path
from typing import List

from parsers.base import BaseParser
from graph.manager import GraphManager
from runtime.eventbus import Event, EventType
from core.identifiers import (
    make_file_id,
    make_include_placeholder_id,
    make_system_header_id,
)
from core.schema import EdgeKind, NodeType

log = logging.getLogger("depanalyzer.parsers.cpp")

# Try to import tree-sitter C bindings. If unavailable, we'll fallback to regex.
_TREE_SITTER_AVAILABLE = True
try:
    import tree_sitter_c as tsc  # as user example suggests
    from tree_sitter import Language, Parser, Query, QueryCursor

    # Build language the way your environment exposes it (user example: tsc.language())
    C_LANGUAGE = Language(tsc.language())
except (ImportError, AttributeError, TypeError, ValueError, OSError) as err:
    log.debug("tree-sitter C import failed, falling back to regex. (%s)", err)
    _TREE_SITTER_AVAILABLE = False

INCLUDE_QUERY = r"""
(preproc_include
  path: [
    (string_literal)
    (system_lib_string)
  ] @path)
"""

# Regex fallback: match #include "..." or #include <...>
INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^">]+)[">]', flags=re.MULTILINE)


def normalize_node_id(file_path: Path, workspace_root: str) -> str:
    """Normalize a file path to a node ID.

    Args:
        file_path: Path to normalize.
        workspace_root: Workspace root directory.

    Returns:
        Normalized node ID.
    """
    try:
        rel_path = file_path.relative_to(workspace_root)
        return str(rel_path).replace("\\", "/")
    except ValueError:
        # File is outside workspace
        return str(file_path)


# DEPRECATED: Use normalize_node_id instead
# def normalize_code_path(file_path: Path, repo_root: str) -> str:


def normalize_include_path(include_name: str, source_file: Path, repo_root: str) -> str:
    """Return a normalized node ID for an include path.

    Args:
        include_name: The include specifier as written in source, e.g. "foo.h".
        source_file: Path to the source file that declares the include.
        repo_root: Absolute path string to the repository root directory.

    Returns:
        A normalized node identifier string for the included target.
    """
    repo_path = Path(repo_root)

    # System includes (angle brackets) - treat as system headers
    if include_name.startswith("/") or include_name in [
        "stdio.h",
        "stdlib.h",
        "string.h",
        "unistd.h",
        "fcntl.h",
        "sys/stat.h",
        "errno.h",
    ]:
        return make_system_header_id(include_name)

    # Try to resolve relative includes
    if include_name.startswith("./") or include_name.startswith("../"):
        try:
            resolved_path = (source_file.parent / include_name).resolve()
            if resolved_path.is_file() and resolved_path.is_relative_to(repo_path):
                return make_file_id(resolved_path, repo_root)
        except (ValueError, OSError):
            pass

    # Look for the include file in common include directories
    potential_paths = [
        source_file.parent / include_name,
        repo_path / include_name,
        repo_path / "include" / include_name,
        repo_path / "src" / include_name,
    ]

    for potential_path in potential_paths:
        try:
            if potential_path.is_file() and potential_path.is_relative_to(repo_path):
                return make_file_id(potential_path, repo_root)
        except (ValueError, OSError):
            continue

    # Default: treat as project-relative include
    return make_include_placeholder_id(include_name)


class CppCodeParser(BaseParser):
    """C/C++ include relationship parser.

    Parses C/C++ files, extracts include directives, creates header/source
    nodes, and connects them with include edges in the shared graph.
    """
    NAME = "c"

    def parse(self, target_path: Path) -> None:
        """Parse a source/header file and update the graph.

        Args:
            target_path: Path to the source or header file to parse.
        """
        # Generate standardized paths
        source_id = normalize_node_id(target_path, str(self.workspace_root))
        src_path = str(target_path.resolve())

        # Create and add the source node with new API
        self.add_node(
            node_id=source_id,
            node_type=NodeType.CODE,
            parser_name=self.NAME,
            src_path=src_path,
            id=source_id,
        )

        try:
            raw = target_path.read_bytes()
        except OSError as exc:
            log.warning("Failed reading %s: %s", target_path, exc)
            return

        includes: List[str] = []

        if _TREE_SITTER_AVAILABLE:
            try:
                parser = Parser(C_LANGUAGE)
                tree = parser.parse(raw)
                query = Query(C_LANGUAGE, INCLUDE_QUERY)
                cursor = QueryCursor(query)

                for match in cursor.matches(tree.root_node):
                    captures = match[1]  # This is a dict: {'capture_name': [nodes]}
                    for capture_name, nodes in captures.items():
                        if capture_name == "path":
                            for node in nodes:
                                try:
                                    text = node.text.decode("utf8").strip('"<>')
                                    if text:
                                        includes.append(text)
                                except UnicodeDecodeError:
                                    # best-effort: ignore nodes that cannot decode
                                    continue
            except (ValueError, TypeError, RuntimeError) as exc:
                # If any tree-sitter step fails, fallback to regex
                log.debug(
                    "tree-sitter parse/query failed for %s: %s. Falling back to regex.",
                    target_path,
                    exc,
                )
                includes = INCLUDE_RE.findall(raw.decode("utf8", errors="ignore"))
        else:
            # regex fallback
            text = raw.decode("utf8", errors="ignore")
            includes = INCLUDE_RE.findall(text)

        # Deduplicate includes while preserving order
        seen = set()
        uniq_includes = []
        for inc in includes:
            if inc not in seen:
                uniq_includes.append(inc)
                seen.add(inc)

        # Add header nodes and edges
        for inc in uniq_includes:
            # Generate standardized target path for the include
            target_id = normalize_include_path(inc, target_path, str(self.workspace_root))

            # Determine header type based on target path
            if target_id.startswith("//system:"):
                header_type = "system_header"
            elif target_id.startswith("//include:"):
                header_type = "project_header"
            else:
                header_type = "code"  # Actual source file being included

            # Create header node with new API
            self.add_node(
                node_id=target_id,
                node_type=header_type,
                parser_name=self.NAME,
                id=target_id,
            )

            # Create edge with new API
            self.add_edge(
                source=source_id,
                target=target_id,
                edge_kind=EdgeKind.INCLUDE,
                parser_name=self.NAME,
            )
