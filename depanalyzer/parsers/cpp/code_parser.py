"""C/C++ code parser using BaseCodeParser interface.

Extracts include dependencies from C/C++ source files using tree-sitter parsing
with regex fallback. Designed for process-pool execution.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List

from depanalyzer.parsers.base import BaseCodeParser

logger = logging.getLogger("depanalyzer.parsers.cpp.code_parser")

# Try to import tree-sitter C bindings
_TREE_SITTER_AVAILABLE = True
try:
    import tree_sitter_c as tsc
    from tree_sitter import Language, Parser, Query, QueryCursor

    C_LANGUAGE = Language(tsc.language())
except (ImportError, AttributeError, TypeError, ValueError, OSError) as err:
    logger.debug("tree-sitter C import failed, falling back to regex: %s", err)
    _TREE_SITTER_AVAILABLE = False

# Tree-sitter query for include directives
INCLUDE_QUERY = r"""
(preproc_include
  path: [
    (string_literal)
    (system_lib_string)
  ] @path)
"""

# Regex fallback for #include directives
INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^">]+)[">]', flags=re.MULTILINE)


class CppCodeParser(BaseCodeParser):
    """C/C++ code parser for extracting include dependencies.

    Pure function implementation suitable for process pool execution.
    Uses tree-sitter when available, falls back to regex parsing.
    """

    NAME = "cpp_code"
    ECOSYSTEM = "cpp"
    CODE_GLOBS = [
        "**/*.c",
        "**/*.cpp",
        "**/*.cc",
        "**/*.cxx",
        "**/*.c++",
        "**/*.h",
        "**/*.hpp",
        "**/*.hh",
        "**/*.hxx",
        "**/*.h++",
    ]

    def parse_file(self, file_path: Path) -> Dict[str, Any]:
        """Parse a C/C++ source file to extract includes.

        This is a pure function that can safely execute in worker processes.

        Args:
            file_path: Path to C/C++ source file.

        Returns:
            Dict[str, Any]: Parse result with structure:
                {
                    "file": str,
                    "includes": List[Path],
                    "root": str,  # "tree-sitter" or "regex"
                }
        """
        # Include minimal metadata so downstream code can specialize handling
        result: Dict[str, Any] = {
            "file": str(file_path),
            "ecosystem": self.ECOSYSTEM,
            "parser_name": self.NAME,
        }

        try:
            raw = file_path.read_bytes()
        except OSError as exc:
            logger.warning("Failed reading %s: %s", file_path, exc)
            return {**result, "error": str(exc)}

        includes: List[str] = []

        # Try tree-sitter parsing first
        if _TREE_SITTER_AVAILABLE:
            try:
                parser = Parser(C_LANGUAGE)
                tree = parser.parse(raw)
                query = Query(C_LANGUAGE, INCLUDE_QUERY)

                # Compatibility with different tree-sitter Python bindings.
                # Prefer the legacy QueryCursor.captures() path (returns a
                # capture-name keyed mapping in this codebase), and fall back
                # to Query.captures(...) if available.
                try:
                    cursor = QueryCursor(query)
                    capture_dict = cursor.captures(tree.root_node)

                    path_nodes = []
                    if isinstance(capture_dict, dict):
                        path_nodes = capture_dict.get("path", [])
                    else:
                        # Some bindings return an iterable of (node, capture_name)
                        for node, capture_name in capture_dict:
                            if capture_name == "path":
                                path_nodes.append(node)

                    for node in path_nodes:
                        try:
                            text = node.text.decode("utf8").strip('"<>')
                            if text:
                                includes.append(text)
                        except UnicodeDecodeError:
                            continue

                except AttributeError:
                    # Fallback to Query.captures API if Cursor.captures is not
                    # available in the installed tree-sitter bindings.
                    for node, capture_name in query.captures(tree.root_node):
                        if capture_name != "path":
                            continue
                        try:
                            text = node.text.decode("utf8").strip('"<>')
                            if text:
                                includes.append(text)
                        except UnicodeDecodeError:
                            continue

                result["root"] = "tree-sitter"
                logger.debug(
                    "Parsed %s with tree-sitter: %d includes",
                    file_path.name,
                    len(includes),
                )

            except (ValueError, TypeError, RuntimeError, AttributeError) as exc:
                logger.debug(
                    "tree-sitter parse failed for %s: %s, falling back to regex",
                    file_path,
                    exc,
                )
                # Fall through to regex parsing
                text = raw.decode("utf8", errors="ignore")
                includes = INCLUDE_RE.findall(text)
                result["root"] = "regex"
        else:
            # Regex fallback
            text = raw.decode("utf8", errors="ignore")
            includes = INCLUDE_RE.findall(text)
            result["root"] = "regex"

        # Deduplicate includes while preserving order
        seen = set()
        unique_includes = []
        for inc in includes:
            if inc not in seen:
                unique_includes.append(inc)
                seen.add(inc)

        # Convert to Path objects
        result["includes"] = [Path(inc) for inc in unique_includes]

        logger.debug(
            "Parsed %s: %d unique includes (method: %s)",
            file_path.name,
            len(unique_includes),
            result.get("root", "unknown")
        )

        return result
