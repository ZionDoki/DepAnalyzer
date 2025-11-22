"""Identifier helpers for graph node IDs.

This module replaces the legacy `depanalyzer.core.identifiers` and provides
a single place to construct canonical node identifiers used across parsers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union


def _normalize_node_id_impl(
    file_path: Path, repo_root: str, target_name: Optional[str] = None
) -> str:
    """Normalize a node ID to a canonical form.

    Args:
        file_path: Absolute or relative file path.
        repo_root: Repository root path.
        target_name: Optional target name (e.g., CMake target).

    Returns:
        Canonical ID like `//relative/path` or `//relative/path:target`.
    """
    repo_path = Path(repo_root).resolve()
    file_path = Path(file_path).resolve()

    try:
        # Try to make path relative to repo_root
        rel_path = file_path.relative_to(repo_path)
        base_id = f"//{rel_path.as_posix()}"
        return f"{base_id}:{target_name}" if target_name else base_id
    except ValueError:
        # File is outside the repo
        try:
            # Try to make path relative to parent of repo_root (matches utils/path_utils.py)
            parent_rel = file_path.relative_to(repo_path.parent)
            base_id = f"//../{parent_rel.as_posix()}"
            return f"{base_id}:{target_name}" if target_name else base_id
        except ValueError:
            # Path is completely external, use just filename as fallback
            base_id = f"//external/{file_path.name}"
            return f"{base_id}:{target_name}" if target_name else base_id


def normalize_node_id(
    file_path: Union[Path, str], repo_root: str, target_name: Optional[str] = None
) -> str:
    """Return canonical file or target ID.

    Args:
        file_path: Absolute or relative path to a file or directory.
        repo_root: Absolute repository root path.
        target_name: Optional target suffix to append after a colon.

    Returns:
        Canonical ID, typically of the form `//relative/path` or
        `//relative/path:target` when a `target_name` is provided.
    """
    return _normalize_node_id_impl(Path(file_path), repo_root, target_name)


def make_external_id(specifier: str) -> str:
    """Create an external placeholder ID.

    Args:
        specifier: Arbitrary external reference, such as a package path or URL.

    Returns:
        A canonical external placeholder ID like `//external:<specifier>`.
    """
    return f"//external:{specifier}"


def make_system_header_id(header_name: str) -> str:
    """Create a system header ID.

    Args:
        header_name: Header file name (e.g. "stdio.h").

    Returns:
        A canonical system header ID like `//system:stdio.h`.
    """
    return f"//system:{header_name}"


def make_include_placeholder_id(include_name: str) -> str:
    """Create a project include placeholder ID.

    Args:
        include_name: Include token as written in source (e.g. "foo/bar.h").

    Returns:
        A canonical include placeholder ID like `//include:foo/bar.h`.
    """
    return f"//include:{include_name}"


def make_file_id(
    file_path: Union[Path, str], repo_root: str, target_name: Optional[str] = None
) -> str:
    """Create a canonical file or target ID.

    Args:
        file_path: Absolute or relative path to a file or directory.
        repo_root: Absolute repository root path.
        target_name: Optional target suffix to append after a colon.

    Returns:
        Canonical ID matching the repository‑relative convention.
    """
    return normalize_node_id(file_path, repo_root, target_name)
