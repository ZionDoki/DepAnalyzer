"""Optimized file scanner using scandir and generator pattern."""

import fnmatch
import logging
import os
from pathlib import Path
from typing import Generator, List, Optional, Set

logger = logging.getLogger("depanalyzer.utils.scanner")


def load_gitignore_patterns(root_path: Path) -> List[str]:
    """Load patterns from .gitignore in the root path."""
    gitignore = root_path / ".gitignore"
    patterns = []
    if gitignore.exists():
        try:
            with open(gitignore, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        patterns.append(line)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Failed to read .gitignore at %s: %s", gitignore, exc)
    return patterns


def _is_ignored(path: Path, root_path: Path, ignore_patterns: List[str]) -> bool:
    """Check if path matches any ignore pattern.

    This is a simplified implementation of gitignore logic.
    It checks the relative path against glob patterns.
    """
    rel_path = path.relative_to(root_path)
    str_path = str(rel_path).replace(os.sep, "/")

    for pattern in ignore_patterns:
        # Handle directory matches (ending with /)
        if pattern.endswith("/") and path.is_dir():
            p = pattern.rstrip("/")
            if fnmatch.fnmatch(str_path, p) or fnmatch.fnmatch(str_path, f"**/{p}"):
                return True

        # Handle generic matches
        if fnmatch.fnmatch(str_path, pattern) or fnmatch.fnmatch(
            str_path, f"**/{pattern}"
        ):
            return True

        # Handle directory prefix matches (e.g. dist/)
        if path.is_dir() and pattern.endswith("/"):
            if str_path.startswith(pattern):
                return True

    return False


def scan_files(
    root_path: Path,
    patterns: List[str],
    ignore_patterns: Optional[List[str]] = None,
    recursive: bool = True,
) -> Generator[Path, None, None]:
    """Scan files matching patterns, respecting ignores.

    Args:
        root_path: Root directory to scan.
        patterns: List of glob patterns to include (e.g. ['*.cpp', 'CMakeLists.txt']).
        ignore_patterns: List of glob patterns to ignore.
        recursive: Whether to scan recursively.

    Yields:
        Path objects for matching files.
    """
    root_path = root_path.resolve()
    ignores = (ignore_patterns or []) + [".git", ".svn", ".hg", "__pycache__"]

    # Pre-process patterns to separate strict filename matches vs extensions
    # This helps optimization (only check extension if pattern is *.ext)

    # Walk using os.walk or efficient recursion
    # We use a stack-based iteration for control

    stack = [root_path]

    while stack:
        current_dir = stack.pop()

        try:
            # Sort for deterministic order
            entries = sorted(os.scandir(current_dir), key=lambda e: e.name)
        except PermissionError:
            continue

        dirs = []
        files = []

        for entry in entries:
            path = Path(entry.path)

            # Check ignore
            if _is_ignored(path, root_path, ignores):
                continue

            if entry.is_dir():
                if recursive:
                    dirs.append(path)
            else:
                files.append(path)

        # Add dirs to stack (reversed to maintain order when popping)
        stack.extend(reversed(dirs))

        # Check files against patterns
        for file_path in files:
            matched = False
            rel_path = file_path.relative_to(root_path)
            str_path = str(rel_path).replace(os.sep, "/")

            for pattern in patterns:
                if fnmatch.fnmatch(file_path.name, pattern) or fnmatch.fnmatch(
                    str_path, pattern
                ):
                    matched = True
                    break

            if matched:
                yield file_path
