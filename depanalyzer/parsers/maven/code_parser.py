"""Java code parser for Maven ecosystem using tree-sitter.

Uses tree-sitter for parsing when available, falls back to regex parsing.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from depanalyzer.parsers.base import BaseCodeParser

logger = logging.getLogger("depanalyzer.parsers.maven.code_parser")

_TREE_SITTER_AVAILABLE = True
try:
    import tree_sitter_java as tsj
    from tree_sitter import Language, Parser, Query, QueryCursor

    JAVA_LANGUAGE = Language(tsj.language())
except (ImportError, AttributeError, OSError, TypeError) as err:  # pragma: no cover
    logger.debug("tree-sitter Java import failed: %s", err)
    _TREE_SITTER_AVAILABLE = False
    JAVA_LANGUAGE = None


# Tree-sitter queries
IMPORT_QUERY = """
    (import_declaration (scoped_identifier) @import_name)
"""

PACKAGE_QUERY = """
    (package_declaration (scoped_identifier) @package_name)
"""

# Regex fallback patterns for Java import statements
IMPORT_RE = re.compile(
    r'^\s*import\s+(?:static\s+)?([a-zA-Z_][\w.]*(?:\.\*)?)\s*;',
    flags=re.MULTILINE
)

PACKAGE_RE = re.compile(
    r'^\s*package\s+([a-zA-Z_][\w.]*)\s*;',
    flags=re.MULTILINE
)


class MavenCodeParser(BaseCodeParser):
    """Java code parser extracting imports and JNI load hints."""

    NAME = "maven_code"
    ECOSYSTEM = "maven"
    CODE_GLOBS = ["**/*.java"]

    def __init__(self, config: Any | None = None) -> None:
        self._config = config

    def parse_file(self, file_path: Path) -> Dict[str, Any]:
        """Parse a Java file and extract imports/native loads.

        Uses tree-sitter when available, falls back to regex parsing.
        """
        result: Dict[str, Any] = {
            "file": str(file_path),
            "ecosystem": self.ECOSYSTEM,
            "parser_name": self.NAME,
        }

        try:
            content = file_path.read_bytes()
        except OSError as exc:
            return {**result, "error": str(exc)}

        # Try tree-sitter first
        if _TREE_SITTER_AVAILABLE and JAVA_LANGUAGE is not None:
            try:
                parser = Parser(JAVA_LANGUAGE)
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
            except (ValueError, TypeError, RuntimeError) as exc:
                logger.debug(
                    "tree-sitter parse failed for %s, falling back to regex: %s",
                    file_path,
                    exc,
                )

        # Regex fallback
        text = content.decode("utf8", errors="ignore")
        imports, package_name, native_libs = _parse_with_regex(text)

        result.update(
            {
                "imports": imports,
                "includes": imports,
                "package": package_name,
                "native_libs": native_libs,
                "root": "regex",
            }
        )
        return result


def _run_query(tree, content: bytes, query_str: str, capture_name: str) -> List[str]:
    """Run a tree-sitter query and collect captured identifiers."""
    query = Query(JAVA_LANGUAGE, query_str)
    cursor = QueryCursor(query)
    captures = cursor.captures(tree.root_node)

    results: List[str] = []
    
    # Handle dict return from captures (new binding behavior)
    if isinstance(captures, dict):
        nodes = captures.get(capture_name, [])
        for node in nodes:
            try:
                text = content[node.start_byte : node.end_byte].decode("utf8").strip()
                if text:
                    results.append(text)
            except UnicodeDecodeError:
                continue
    # Handle list return from captures (potential older/different binding behavior)
    else:
        for node, name in captures:
            if name != capture_name:
                continue
            try:
                text = content[node.start_byte : node.end_byte].decode("utf8").strip()
                if text:
                    results.append(text)
            except UnicodeDecodeError:
                continue

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
    pattern = re.compile(r'System\.loadLibrary\(\s*"([^"]+)"\s*\)')
    for match in pattern.finditer(text):
        libs.add(match.group(1))
    return list(libs)


def _parse_with_regex(text: str) -> Tuple[List[str], Optional[str], List[str]]:
    """Parse Java source using regex patterns.

    Args:
        text: Java source code as string.

    Returns:
        Tuple of (imports, package_name, native_libs).
    """
    # Extract imports
    imports: List[str] = []
    seen: Set[str] = set()
    for match in IMPORT_RE.finditer(text):
        import_name = match.group(1)
        if import_name not in seen:
            seen.add(import_name)
            imports.append(import_name)

    # Extract package
    package_match = PACKAGE_RE.search(text)
    package_name = package_match.group(1) if package_match else None

    # Extract native libs
    native_libs = _extract_native_libs(text)

    return imports, package_name, native_libs


__all__ = ["MavenCodeParser"]
