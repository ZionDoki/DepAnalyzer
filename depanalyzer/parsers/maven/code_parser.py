"""Java code parser for Maven ecosystem using tree-sitter."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Set

from depanalyzer.parsers.base import BaseCodeParser

logger = logging.getLogger("depanalyzer.parsers.maven.code_parser")

_TREE_SITTER_AVAILABLE = True
try:
    import tree_sitter_java as tsj
    from tree_sitter import Language, Parser, Query

    JAVA_LANGUAGE = Language(tsj.language())
except (ImportError, AttributeError, OSError, TypeError) as err:  # pragma: no cover
    logger.debug("tree-sitter Java import failed: %s", err)
    _TREE_SITTER_AVAILABLE = False
    JAVA_LANGUAGE = None


IMPORT_QUERY = """
    (import_declaration
        name: (_) @import_name)
"""

PACKAGE_QUERY = """
    (package_declaration
        name: (_) @package_name)
"""


class MavenCodeParser(BaseCodeParser):
    """Java code parser extracting imports and JNI load hints."""

    NAME = "maven_code"
    ECOSYSTEM = "maven"
    CODE_GLOBS = ["**/*.java"]

    def __init__(self, config: Any | None = None) -> None:
        self._config = config

    def parse_file(self, file_path: Path) -> Dict[str, Any]:
        """Parse a Java file and extract imports/native loads."""
        result: Dict[str, Any] = {
            "file": str(file_path),
            "ecosystem": self.ECOSYSTEM,
            "parser_name": self.NAME,
        }

        if not _TREE_SITTER_AVAILABLE or JAVA_LANGUAGE is None:
            return {**result, "error": "tree-sitter Java language not available"}

        try:
            content = file_path.read_bytes()
        except OSError as exc:
            return {**result, "error": str(exc)}

        try:
            parser = Parser()
            parser.set_language(JAVA_LANGUAGE)
            tree = parser.parse(content)

            imports = _run_query(tree, content, IMPORT_QUERY, "import_name")
            package_values = _run_query(tree, content, PACKAGE_QUERY, "package_name")
            package_name = package_values[0] if package_values else None

            native_libs = _extract_native_libs(content.decode("utf8", errors="ignore"))

            result.update(
                {
                    "imports": imports,
                    "includes": imports,
                    "package": package_name,
                    "native_libs": native_libs,
                    "root": "tree-sitter",
                }
            )
            return result
        except (ValueError, TypeError, RuntimeError) as exc:  # pragma: no cover
            logger.error("tree-sitter parse failed for %s: %s", file_path, exc)
            return {**result, "error": str(exc)}


def _run_query(tree, content: bytes, query_str: str, capture_name: str) -> List[str]:
    """Run a tree-sitter query and collect captured identifiers."""
    query = Query(JAVA_LANGUAGE, query_str)
    captures = query.captures(tree.root_node)
    results: List[str] = []
    for node, name in captures:
        if name != capture_name:
            continue
        try:
            text = content[node.start_byte : node.end_byte].decode("utf8").strip()
        except UnicodeDecodeError:
            continue
        if text:
            results.append(text)

    # Deduplicate while preserving order
    seen: Set[str] = set()
    unique: List[str] = []
    for item in results:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _extract_native_libs(text: str) -> List[str]:
    """Extract System.loadLibrary arguments."""
    libs: Set[str] = set()
    pattern = re.compile(r"System\\.loadLibrary\\(\\s*\"([^\"]+)\"\\s*\\)")
    for match in pattern.finditer(text):
        libs.add(match.group(1))
    return list(libs)


__all__ = ["MavenCodeParser"]
