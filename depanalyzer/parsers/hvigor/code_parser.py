"""ArkTS/HVigor code parser using BaseCodeParser interface.

Extracts import dependencies from ArkTS/TypeScript source files using tree-sitter.
Designed for process-pool execution.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from depanalyzer.parsers.base import BaseCodeParser

logger = logging.getLogger("depanalyzer.parsers.hvigor.code_parser")

# Try to import tree-sitter TypeScript bindings
_TREE_SITTER_AVAILABLE = True
try:
    import tree_sitter_typescript as tst
    from tree_sitter import Language, Parser, Query, QueryCursor

    TS_LANGUAGE = Language(tst.language_typescript())
except (ImportError, OSError, TypeError) as err:
    logger.debug("tree-sitter TypeScript import failed: %s", err)
    _TREE_SITTER_AVAILABLE = False
    TS_LANGUAGE = None

# Tree-sitter query for import/export statements
IMPORT_QUERY = """
    (import_statement source: (string) @path)
    (export_statement source: (string) @path)
"""


class HvigorCodeParser(BaseCodeParser):
    """ArkTS/HVigor code parser for extracting import dependencies.

    Pure function implementation suitable for process pool execution.
    Uses tree-sitter for TypeScript/ArkTS parsing.
    """

    NAME = "hvigor_code"
    ECOSYSTEM = "hvigor"
    CODE_GLOBS = ["**/*.ets", "**/*.ts", "**/*.js"]

    def parse_file(self, file_path: Path) -> Dict[str, Any]:
        """Parse an ArkTS/TypeScript file to extract imports.

        This is a pure function that can safely execute in worker processes.

        Args:
            file_path: Path to ArkTS/TypeScript source file.

        Returns:
            Dict[str, Any]: Parse result with structure:
                {
                    "file": str,
                    "imports": List[str],  # Resolved import paths
                    "unresolved": List[str],  # Unresolved import specifiers
                    "root": str,  # "tree-sitter"
                }
        """
        result = {"file": str(file_path)}

        if not _TREE_SITTER_AVAILABLE or TS_LANGUAGE is None:
            return {
                **result,
                "error": "tree-sitter TypeScript language not available"
            }

        try:
            content = file_path.read_bytes()
        except OSError as exc:
            logger.warning("Failed reading %s: %s", file_path, exc)
            return {**result, "error": str(exc)}

        try:
            # Parse with tree-sitter
            ts_parser = Parser(TS_LANGUAGE)
            tree = ts_parser.parse(content)

            # Query for import/export statements
            query = Query(TS_LANGUAGE, IMPORT_QUERY)
            cursor = QueryCursor(query)
            capture_dict = cursor.captures(tree.root_node)

            import_specifiers: Set[str] = set()

            # Extract import specifiers
            if "path" in capture_dict:
                for node in capture_dict["path"]:
                    dep_str = node.text.decode("utf8").strip("\"'")
                    # Skip @ohos system imports
                    if not dep_str.startswith("@ohos"):
                        import_specifiers.add(dep_str)

            # Resolve imports to file paths
            resolved_imports: List[str] = []
            unresolved_imports: List[str] = []

            for dep_str in import_specifiers:
                resolved_path = self._resolve_import(file_path, dep_str)
                if resolved_path and resolved_path.is_file():
                    resolved_imports.append(str(resolved_path))
                else:
                    unresolved_imports.append(dep_str)

            result["imports"] = resolved_imports
            result["unresolved"] = unresolved_imports
            result["root"] = "tree-sitter"

            logger.debug(
                "Parsed %s: %d resolved imports, %d unresolved",
                file_path.name,
                len(resolved_imports),
                len(unresolved_imports)
            )

            return result

        except (TypeError, ValueError, RuntimeError) as exc:
            logger.error("tree-sitter parse failed for %s: %s", file_path, exc)
            return {**result, "error": str(exc)}

    def _resolve_import(self, source_file: Path, import_spec: str) -> Optional[Path]:
        """Resolve an import specifier to an absolute file path.

        Args:
            source_file: Path to the source file containing the import.
            import_spec: Import specifier string (e.g., "./foo", "../bar").

        Returns:
            Optional[Path]: Resolved absolute path, or None if not found.
        """
        # Relative imports
        if import_spec.startswith(("./", "../")):
            base_path = (source_file.parent / import_spec).resolve()
        else:
            # Absolute/module imports (resolve from project root)
            # Note: In process pool, we don't have access to repo_root
            # So we resolve relative to source file's parent as best effort
            base_path = source_file.parent / import_spec

        # Try with common extensions
        for ext in [".ets", ".ts", ".js"]:
            candidate = base_path.with_suffix(ext)
            if candidate.is_file():
                return candidate

        # Try as directory with index file
        if base_path.is_dir():
            for ext in [".ets", ".ts", ".js"]:
                index_file = base_path / f"index{ext}"
                if index_file.is_file():
                    return index_file

        return None
