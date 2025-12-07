"""Path normalization utilities for cross-language dependency graph."""
from pathlib import Path
from typing import Optional, Union


def normalize_node_id(
    path: Union[Path, str],
    root_path: Path,
    namespace: Optional[str] = None,
) -> str:
    """
    Normalize node ID to unified format: //relative_path or //../external_path.

    All node IDs in the dependency graph use this format for consistency:
    - Internal paths (within root_path): //module/src/file.cpp
    - External paths (outside root_path): //../external_lib/file.h

    Args:
        path: Absolute or relative path to normalize
        root_path: Root path of the analysis target

    Returns:
        Normalized node ID string

    Examples:
        >>> root = Path("/workspace/project")
        >>> normalize_node_id(Path("/workspace/project/module/app"), root)
        '//module/app'
        >>> normalize_node_id(Path("/workspace/external/.gitignore"), root)
        '//../external/.gitignore'
    """
    path = Path(path).resolve()
    root_path = Path(root_path).resolve()

    try:
        # Try to make path relative to root_path
        rel_path = path.relative_to(root_path)
        rel_str = str(rel_path).replace("\\", "/")
        if namespace:
            # Third-party or namespaced repository: prefix with namespace
            return "//" + "/".join(
                [namespace] + ([rel_str] if rel_str else [])
            )
        return "//" + rel_str
    except ValueError:
        # Path is outside root_path
        try:
            # Try to make path relative to parent of root_path
            parent_rel = path.relative_to(root_path.parent)
            return "//../" + str(parent_rel).replace("\\", "/")
        except ValueError:
            # Path is completely external, use path hash + filename to avoid collisions
            # Different files with same name in different directories will have unique IDs
            import hashlib
            path_hash = hashlib.sha256(str(path).encode()).hexdigest()[:8]
            return f"//external/{path_hash}/{path.name}"


class PathTraversalError(ValueError):
    """Raised when a path traversal attack is detected."""

    pass


def denormalize_node_id(
    node_id: str,
    root_path: Path,
    validate_boundary: bool = True,
) -> Path:
    """
    Convert normalized node ID back to absolute path.

    Args:
        node_id: Normalized node ID (//... or //../...)
        root_path: Root path of the analysis target
        validate_boundary: If True, validate that the result path stays within
            expected boundaries to prevent path traversal attacks.

    Returns:
        Absolute path

    Raises:
        PathTraversalError: If path traversal is detected and validate_boundary is True.
        ValueError: If node_id format is invalid or //external/ paths are used
            with validate_boundary=True.

    Examples:
        >>> root = Path("/workspace/project")
        >>> denormalize_node_id("//module/app", root)
        Path('/workspace/project/module/app')
        >>> denormalize_node_id("//../external/.gitignore", root)
        Path('/workspace/external/.gitignore')
    """
    root_path = Path(root_path).resolve()

    if node_id.startswith("//../"):
        # External path - relative to parent of root
        rel_path = node_id[len("//../"):]

        # Check for path traversal attempts in the relative path
        if ".." in Path(rel_path).parts:
            raise PathTraversalError(
                f"Path traversal detected in node ID: {node_id}"
            )

        result = (root_path.parent / rel_path).resolve()

        # Validate the result stays within parent directory
        if validate_boundary:
            if not result.is_relative_to(root_path.parent):
                raise PathTraversalError(
                    f"Path escapes allowed boundary: {node_id} -> {result}"
                )

        return result

    elif node_id.startswith("//external/"):
        # Completely external paths are inherently unsafe for denormalization
        # because we don't know the original absolute path
        if validate_boundary:
            raise ValueError(
                f"Cannot safely denormalize external node ID: {node_id}. "
                "Use validate_boundary=False if you understand the risks."
            )
        # Fallback for completely external paths (lossy conversion)
        return Path(node_id[len("//external/"):])

    elif node_id.startswith("//"):
        # Internal path - relative to root
        rel_path = node_id[len("//"):]

        # Check for path traversal attempts in the relative path
        if ".." in Path(rel_path).parts:
            raise PathTraversalError(
                f"Path traversal detected in node ID: {node_id}"
            )

        result = (root_path / rel_path).resolve()

        # Validate the result stays within root directory
        if validate_boundary:
            if not result.is_relative_to(root_path):
                raise PathTraversalError(
                    f"Path escapes root boundary: {node_id} -> {result}"
                )

        return result

    else:
        raise ValueError(f"Invalid node ID format: {node_id}")


def is_external_node(node_id: str) -> bool:
    """
    Check if a node ID represents an external dependency.

    Args:
        node_id: Normalized node ID

    Returns:
        True if the node is external to the analysis root
    """
    return node_id.startswith("//../") or node_id.startswith("//external/")


def get_parent_node_id(node_id: str) -> str:
    """
    Get the parent directory's node ID.

    Args:
        node_id: Normalized node ID

    Returns:
        Parent node ID

    Examples:
        >>> get_parent_node_id("//module/src/file.cpp")
        '//module/src'
        >>> get_parent_node_id("//module")
        '//'
    """
    if node_id == "//" or node_id == "//../":
        return node_id

    parts = node_id.rstrip("/").split("/")
    if len(parts) <= 2:
        # Root level
        return "//"

    return "/".join(parts[:-1])


def join_node_id(base_id: str, *parts: str) -> str:
    """
    Join node ID parts.

    Args:
        base_id: Base node ID
        *parts: Path parts to append

    Returns:
        Combined node ID

    Examples:
        >>> join_node_id("//module", "src", "file.cpp")
        '//module/src/file.cpp'
    """
    result = base_id.rstrip("/")
    for part in parts:
        result += "/" + part.lstrip("/")
    return result
