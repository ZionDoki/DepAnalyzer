"""ArkTS/HVigor code parser using BaseCodeParser interface.

Extracts import dependencies from ArkTS/TypeScript source files using tree-sitter.
Designed for process-pool execution.
"""

# Parser intentionally swallows tree-sitter failures so the pool keeps running.


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

    def __init__(self, config: Optional[Any] = None) -> None:
        """Initialize Hvigor code parser.

        Args:
            config: Optional configuration slice controlling parser behaviour.
        """
        # NOTE: Code parsers are designed to be effectively stateless when
        # used from the process pool. We only keep simple, immutable config
        # such as numeric limits.
        self._config = config
        max_levels = 8
        if config is not None:
            try:
                # Support both dataclass-style objects and plain dicts.
                value = getattr(config, "max_import_ancestor_levels", None)
                if value is None and isinstance(config, dict):
                    value = config.get("max_import_ancestor_levels")
                if isinstance(value, int) and value > 0:
                    max_levels = value
            except Exception:
                # Fall back to compiled default on any error
                pass
        self._max_ancestor_levels = max_levels

    def parse_file(self, file_path: Path) -> Dict[str, Any]:
        """Parse an ArkTS/TypeScript file to extract imports.

        This is a pure function that can safely execute in worker processes.

        Args:
            file_path: Path to ArkTS/TypeScript source file.

        Returns:
            Dict[str, Any]: Parse result with structure:
                {
                    "file": str,
                    "includes": List[str],  # Resolved import paths (for graph edges)
                    "imports": List[str],  # Resolved import paths
                    "unresolved": List[str],  # Unresolved import specifiers
                    "root": str,  # "tree-sitter"
                }
        """
        # Basic metadata used by downstream graph construction
        result: Dict[str, Any] = {
            "file": str(file_path),
            "ecosystem": self.ECOSYSTEM,
            "parser_name": self.NAME,
        }

        if not _TREE_SITTER_AVAILABLE or TS_LANGUAGE is None:
            return {**result, "error": "tree-sitter TypeScript language not available"}

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

            import_specifiers: Set[str] = set()

            # Extract import specifiers from all captures named "path".
            # Prefer the legacy QueryCursor.captures(...) code path used in
            # the original implementation, and fall back to Query.captures(...)
            # when available.
            try:
                cursor = QueryCursor(query)
                capture_dict = cursor.captures(tree.root_node)

                path_nodes = []
                if isinstance(capture_dict, dict):
                    path_nodes = capture_dict.get("path", [])
                else:
                    for node, capture_name in capture_dict:
                        if capture_name == "path":
                            path_nodes.append(node)

                for node in path_nodes:
                    try:
                        dep_str = node.text.decode("utf8").strip("\"'")
                    except UnicodeDecodeError:
                        continue
                    if not dep_str.startswith("@ohos"):
                        import_specifiers.add(dep_str)

            except AttributeError:
                for node, capture_name in query.captures(tree.root_node):
                    if capture_name != "path":
                        continue
                    try:
                        dep_str = node.text.decode("utf8").strip("\"'")
                    except UnicodeDecodeError:
                        continue
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
            result["includes"] = resolved_imports
            result["unresolved"] = unresolved_imports
            result["root"] = "tree-sitter"

            logger.debug(
                "Parsed %s: %d resolved imports, %d unresolved",
                file_path.name,
                len(resolved_imports),
                len(unresolved_imports),
            )

            return result

        except (TypeError, ValueError, RuntimeError, AttributeError) as exc:
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
            # Absolute/module imports.
            #
            # In the worker process we do not know the repository root, so we:
            # 1) First try resolving relative to the current file's directory.
            # 2) If that fails, conservatively walk up ancestor directories and
            #    try "<ancestor>/<import_spec>".
            #
            # This mirrors the old implementation that resolved absolute paths
            # from the repo root and significantly reduces "false isolated"
            # ArkTS nodes in monorepo-style layouts.
            base_path = source_file.parent / import_spec

        exts = [".ets", ".ts", ".js"]

        # Try with common extensions relative to the chosen base path
        for ext in exts:
            candidate = base_path.with_suffix(ext)
            if candidate.is_file():
                return candidate

        # Try as directory with index file
        if base_path.is_dir():
            for ext in exts:
                index_file = base_path / f"index{ext}"
                if index_file.is_file():
                    return index_file

            # Fallback for "module-like" imports in monorepos:
        # if the specifier looks path-like (contains a slash or explicit
        # extension) and is not an external package (e.g. @ohos/...), walk
        # up the ancestor chain and search there. This helps resolve imports
        # such as "entry/src/common/util" that are effectively rooted at the
        # project root or module root.
        if not import_spec.startswith("@") and (
            "/" in import_spec or import_spec.endswith(tuple(exts))
        ):
            current = source_file.parent
            # Cap the number of levels we walk to avoid traversing the whole
            # filesystem hierarchy in deeply nested setups.
            max_levels = getattr(self, "_max_ancestor_levels", 8)
            levels = 0
            while True:
                parent = current.parent
                if parent == current or levels >= max_levels:
                    break
                # Try resolving from this ancestor
                ancestor_base = (parent / import_spec).resolve()

                for ext in exts:
                    candidate = ancestor_base.with_suffix(ext)
                    if candidate.is_file():
                        return candidate

                if ancestor_base.is_dir():
                    for ext in exts:
                        index_file = ancestor_base / f"index{ext}"
                        if index_file.is_file():
                            return index_file

                current = parent
                levels += 1

        return None
