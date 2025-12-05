"""NPM code parser for JavaScript/TypeScript files.

Uses tree-sitter to parse JS/TS source files and extract import/require
statements for dependency analysis.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from depanalyzer.parsers.base import BaseCodeParser, ParseError

logger = logging.getLogger("depanalyzer.parsers.npm.code_parser")

# Try to import tree-sitter
try:
    import tree_sitter_javascript as ts_javascript
    import tree_sitter_typescript as ts_typescript
    from tree_sitter import Language, Parser

    HAS_TREE_SITTER = True
except ImportError:
    HAS_TREE_SITTER = False
    logger.warning(
        "tree-sitter or language bindings not available, "
        "falling back to regex-based parsing"
    )


class NpmCodeParser(BaseCodeParser):
    """Code parser for JavaScript/TypeScript files.

    Extracts:
    - ES6 imports: import x from 'module'
    - CommonJS requires: const x = require('module')
    - Dynamic imports: import('module')
    - Re-exports: export * from 'module'
    """

    NAME = "npm_code_parser"
    ECOSYSTEM = "npm"
    CODE_GLOBS = [
        "**/*.js",
        "**/*.mjs",
        "**/*.cjs",
        "**/*.jsx",
        "**/*.ts",
        "**/*.tsx",
        "**/*.mts",
        "**/*.cts",
    ]

    # Patterns to ignore
    IGNORE_PATTERNS = [
        "**/node_modules/**",
        "**/dist/**",
        "**/build/**",
        "**/out/**",
        "**/*.min.js",
        "**/*.bundle.js",
    ]

    def __init__(self) -> None:
        """Initialize the code parser with tree-sitter parsers."""
        self._js_parser: Optional[Parser] = None
        self._ts_parser: Optional[Parser] = None
        self._tsx_parser: Optional[Parser] = None

        if HAS_TREE_SITTER:
            self._init_parsers()

    def _init_parsers(self) -> None:
        """Initialize tree-sitter parsers for JS and TS."""
        try:
            # JavaScript parser
            self._js_parser = Parser(Language(ts_javascript.language()))

            # TypeScript parser
            self._ts_parser = Parser(Language(ts_typescript.language_typescript()))

            # TSX parser
            self._tsx_parser = Parser(Language(ts_typescript.language_tsx()))

            logger.debug("NpmCodeParser: tree-sitter parsers initialized")
        except Exception as e:
            logger.warning("Failed to initialize tree-sitter parsers: %s", e)
            self._js_parser = None
            self._ts_parser = None
            self._tsx_parser = None

    def parse_file(self, file_path: Path) -> Dict[str, Any]:
        """Parse a JavaScript/TypeScript source file.

        Args:
            file_path: Path to the source file.

        Returns:
            Dict containing:
                - file: str - file path
                - ecosystem: str - "npm"
                - parser_name: str - parser name
                - imports: List[str] - imported module specifiers
                - exports: List[str] - exported names
                - unresolved: List[str] - unresolved imports
                - root: str - parsing method used
                - error: Optional[str] - error message if any
        """
        result = {
            "file": str(file_path),
            "ecosystem": self.ECOSYSTEM,
            "parser_name": self.NAME,
            "imports": [],
            "exports": [],
            "unresolved": [],
            "root": "unknown",
            "error": None,
        }

        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = file_path.read_text(encoding="latin-1")
            except Exception as e:
                result["error"] = f"Cannot read file: {e}"
                return result
        except OSError as e:
            result["error"] = f"Cannot read file: {e}"
            return result

        # Determine file type
        suffix = file_path.suffix.lower()
        is_typescript = suffix in (".ts", ".tsx", ".mts", ".cts")
        is_tsx = suffix == ".tsx"
        is_jsx = suffix == ".jsx"

        # Try tree-sitter first
        if HAS_TREE_SITTER and self._js_parser:
            try:
                imports, exports = self._parse_with_tree_sitter(
                    content, is_typescript, is_tsx or is_jsx
                )
                result["imports"] = imports
                result["exports"] = exports
                result["root"] = "tree-sitter"
                return result
            except Exception as e:
                logger.debug(
                    "tree-sitter parsing failed for %s, falling back to regex: %s",
                    file_path,
                    e,
                )

        # Fallback to regex
        imports, exports = self._parse_with_regex(content)
        result["imports"] = imports
        result["exports"] = exports
        result["root"] = "regex"

        return result

    def _parse_with_tree_sitter(
        self,
        content: str,
        is_typescript: bool,
        is_tsx: bool,
    ) -> tuple[List[str], List[str]]:
        """Parse source code using tree-sitter.

        Args:
            content: Source code content.
            is_typescript: Whether the file is TypeScript.
            is_tsx: Whether the file is TSX/JSX.

        Returns:
            Tuple of (imports, exports) lists.
        """
        # Select appropriate parser
        if is_tsx:
            parser = self._tsx_parser or self._ts_parser or self._js_parser
        elif is_typescript:
            parser = self._ts_parser or self._js_parser
        else:
            parser = self._js_parser

        if not parser:
            raise RuntimeError("No parser available")

        tree = parser.parse(content.encode("utf-8"))
        root = tree.root_node

        imports: List[str] = []
        exports: List[str] = []

        self._extract_imports_from_tree(root, content, imports)
        self._extract_exports_from_tree(root, content, exports)

        return imports, exports

    def _extract_imports_from_tree(
        self,
        node: Any,
        content: str,
        imports: List[str],
    ) -> None:
        """Recursively extract import statements from AST.

        Args:
            node: Tree-sitter node.
            content: Source code content.
            imports: List to append imports to.
        """
        node_type = node.type

        # ES6 import declaration: import x from 'module'
        if node_type == "import_statement":
            source = self._find_child_by_type(node, "string")
            if source:
                module_spec = self._extract_string_value(source, content)
                if module_spec:
                    imports.append(module_spec)

        # CommonJS require: require('module')
        elif node_type == "call_expression":
            func = node.child_by_field_name("function")
            if func and func.type == "identifier":
                func_name = content[func.start_byte : func.end_byte]
                if func_name == "require":
                    args = node.child_by_field_name("arguments")
                    if args and args.child_count > 0:
                        first_arg = args.child(0)
                        # Skip opening paren
                        if first_arg and first_arg.type == "(":
                            first_arg = args.child(1)
                        if first_arg and first_arg.type in ("string", "template_string"):
                            module_spec = self._extract_string_value(first_arg, content)
                            if module_spec:
                                imports.append(module_spec)

        # Dynamic import: import('module')
        elif node_type == "import":
            # This is the import keyword in dynamic import
            pass

        # Recurse into children
        for child in node.children:
            self._extract_imports_from_tree(child, content, imports)

    def _extract_exports_from_tree(
        self,
        node: Any,
        content: str,
        exports: List[str],
    ) -> None:
        """Recursively extract export statements from AST.

        Args:
            node: Tree-sitter node.
            content: Source code content.
            exports: List to append exports to.
        """
        node_type = node.type

        # export * from 'module' or export { x } from 'module'
        if node_type == "export_statement":
            source = self._find_child_by_type(node, "string")
            if source:
                module_spec = self._extract_string_value(source, content)
                if module_spec:
                    exports.append(module_spec)

        # Recurse into children
        for child in node.children:
            self._extract_exports_from_tree(child, content, exports)

    def _find_child_by_type(self, node: Any, child_type: str) -> Optional[Any]:
        """Find first child node of given type.

        Args:
            node: Parent node.
            child_type: Type to search for.

        Returns:
            Child node or None.
        """
        for child in node.children:
            if child.type == child_type:
                return child
        return None

    def _extract_string_value(self, node: Any, content: str) -> Optional[str]:
        """Extract string value from a string node.

        Args:
            node: String node.
            content: Source code content.

        Returns:
            String value without quotes, or None.
        """
        text = content[node.start_byte : node.end_byte]
        # Remove quotes
        if len(text) >= 2:
            if (text.startswith('"') and text.endswith('"')) or (
                text.startswith("'") and text.endswith("'")
            ):
                return text[1:-1]
            if text.startswith("`") and text.endswith("`"):
                return text[1:-1]
        return None

    def _parse_with_regex(self, content: str) -> tuple[List[str], List[str]]:
        """Parse source code using regex patterns.

        Args:
            content: Source code content.

        Returns:
            Tuple of (imports, exports) lists.
        """
        imports: Set[str] = set()
        exports: Set[str] = set()

        # ES6 import patterns
        # import x from 'module'
        # import { x } from 'module'
        # import * as x from 'module'
        # import 'module'
        import_pattern = re.compile(
            r"""import\s+(?:[\w\s{},*]+\s+from\s+)?['"]([^'"]+)['"]""",
            re.MULTILINE,
        )
        for match in import_pattern.finditer(content):
            imports.add(match.group(1))

        # CommonJS require pattern
        # require('module')
        # require("module")
        require_pattern = re.compile(
            r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""",
            re.MULTILINE,
        )
        for match in require_pattern.finditer(content):
            imports.add(match.group(1))

        # Dynamic import pattern
        # import('module')
        dynamic_import_pattern = re.compile(
            r"""import\s*\(\s*['"]([^'"]+)['"]\s*\)""",
            re.MULTILINE,
        )
        for match in dynamic_import_pattern.finditer(content):
            imports.add(match.group(1))

        # Re-export patterns
        # export * from 'module'
        # export { x } from 'module'
        export_pattern = re.compile(
            r"""export\s+(?:[\w\s{},*]+\s+)?from\s+['"]([^'"]+)['"]""",
            re.MULTILINE,
        )
        for match in export_pattern.finditer(content):
            exports.add(match.group(1))

        return list(imports), list(exports)

    def can_handle_file(self, file_path: Path) -> bool:
        """Check if this parser can handle the given file.

        Args:
            file_path: Path to file to check.

        Returns:
            bool: True if file extension matches supported types.
        """
        suffix = file_path.suffix.lower()
        return suffix in (
            ".js",
            ".mjs",
            ".cjs",
            ".jsx",
            ".ts",
            ".tsx",
            ".mts",
            ".cts",
        )
