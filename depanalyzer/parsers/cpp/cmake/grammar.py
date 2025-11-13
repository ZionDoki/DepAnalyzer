"""CMake parser using tree-sitter-cmake.

This module provides a tree-sitter-based CMake parser with a compatibility
wrapper to maintain interface consistency with the existing codebase.
"""

from __future__ import annotations

from typing import Iterator, Any
import tree_sitter_cmake
from tree_sitter import Language, Parser, Node, Tree


class ParseTreeWrapper:
    """Wrapper around tree-sitter Tree for convenient API access.

    This wrapper makes the tree-sitter parse tree easier to work with by
    providing a find_data() method for locating nodes by type.
    """

    def __init__(self, tree: Tree, source_bytes: bytes):
        """Initialize wrapper with tree-sitter tree and source bytes.

        Args:
            tree: tree-sitter Tree object
            source_bytes: Original source code as bytes for text extraction
        """
        self.tree = tree
        self.source_bytes = source_bytes

    def find_data(self, data_name: str) -> Iterator[ParseNodeWrapper]:
        """Find all nodes with the given type name.

        Traverses the tree and yields all nodes matching the specified type.

        Args:
            data_name: Node type to search for (e.g., "command_invocation")

        Yields:
            ParseNodeWrapper objects for each matching node
        """
        def traverse(node: Node) -> Iterator[Node]:
            """Recursively traverse tree and yield matching nodes."""
            if node.type == data_name:
                yield node
            for child in node.children:
                yield from traverse(child)

        root = self.tree.root_node
        for node in traverse(root):
            yield ParseNodeWrapper(node, self.source_bytes)


class ParseNodeWrapper:
    """Wrapper around tree-sitter Node for convenient API access.

    This wrapper makes tree-sitter nodes easier to work with by providing
    a children property that returns appropriate wrapper objects.
    """

    def __init__(self, node: Node, source_bytes: bytes):
        """Initialize wrapper with tree-sitter node and source bytes.

        Args:
            node: tree-sitter Node object
            source_bytes: Original source code as bytes for text extraction
        """
        self.node = node
        self.source_bytes = source_bytes
        self._children = None

    @property
    def children(self) -> list[ParseTokenWrapper | ParseNodeWrapper]:
        """Get node children as a list of wrappers.

        Tree-sitter nodes with named children are wrapped, while leaf nodes
        with text content are converted to ParseTokenWrapper objects.

        Returns:
            List of child node/token wrappers
        """
        if self._children is None:
            self._children = []
            for child in self.node.children:
                # Leaf nodes with text become tokens
                if child.child_count == 0 or child.type in ('identifier', 'argument'):
                    text = self.source_bytes[child.start_byte:child.end_byte].decode('utf8', errors='ignore')
                    self._children.append(ParseTokenWrapper(text, child.type))
                else:
                    # Non-leaf nodes remain wrapped nodes
                    self._children.append(ParseNodeWrapper(child, self.source_bytes))
        return self._children

    def __str__(self) -> str:
        """Get text content of this node."""
        return self.source_bytes[self.node.start_byte:self.node.end_byte].decode('utf8', errors='ignore')


class ParseTokenWrapper:
    """Wrapper for token-like leaf nodes.

    This class provides a simple interface for accessing token text
    and type information from tree-sitter leaf nodes.
    """

    def __init__(self, text: str, token_type: str):
        """Initialize token with text and type.

        Args:
            text: Token text content
            token_type: Token type name
        """
        self.value = text
        self.type = token_type

    def __str__(self) -> str:
        """Return token text."""
        return self.value

    def __repr__(self) -> str:
        """Return token representation."""
        return f"Token({self.type}, {self.value!r})"


class CMakeParser:
    """Tree-sitter based CMake parser.

    This parser uses tree-sitter-cmake for parsing and provides wrapper
    classes to simplify working with the parse tree.
    """

    def __init__(self):
        """Initialize tree-sitter parser with CMake language."""
        self.parser = Parser()
        # Get the CMake language from tree-sitter-cmake
        language = Language(tree_sitter_cmake.language())
        self.parser.language = language

    def parse(self, text: str) -> ParseTreeWrapper:
        """Parse CMake source code.

        Args:
            text: CMake source code as string

        Returns:
            ParseTreeWrapper with convenient API access

        Raises:
            Exception: If parsing fails
        """
        source_bytes = text.encode('utf8')
        tree = self.parser.parse(source_bytes)

        # Check for parse errors
        if tree.root_node.has_error:
            # Find first error node for better error message
            def find_error(node: Node) -> Node | None:
                if node.is_error or node.is_missing:
                    return node
                for child in node.children:
                    error = find_error(child)
                    if error:
                        return error
                return None

            error_node = find_error(tree.root_node)
            if error_node:
                line = source_bytes[:error_node.start_byte].count(b'\n') + 1
                col = error_node.start_byte - source_bytes.rfind(b'\n', 0, error_node.start_byte)
                raise Exception(f"Parse error at line {line}, column {col}")
            else:
                raise Exception("Parse error in CMake file")

        return ParseTreeWrapper(tree, source_bytes)


# Expose a single shared parser instance
CMAKE_PARSER = CMakeParser()
