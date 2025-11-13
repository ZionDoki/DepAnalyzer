"""CMake variable resolver utilities.

Resolves simple `${VAR}` expansions, supports list variables, and provides
globbing helpers for file patterns in the context of a given CMake file.
"""

from __future__ import annotations

import glob
import logging
import os
from pathlib import Path
from typing import Optional

from .tokens import CMAKE_VAR_PATTERN

logger = logging.getLogger("depanalyzer.parsers.cpp.cmake.variables")


class CMakeVariableResolver:
    """CMake variable resolver supporting basic variables and list operations."""

    def __init__(self, repo_root: str, cmake_file: Path):
        self.repo_root = Path(repo_root)
        self.cmake_file = cmake_file
        self.cmake_dir = cmake_file.parent
        self.project_name = None
        self._resolve_depth = 0

        self.variables = {
            "CMAKE_SOURCE_DIR": str(self.repo_root),
            "CMAKE_CURRENT_SOURCE_DIR": str(self.cmake_dir),
            "CMAKE_CURRENT_BINARY_DIR": str(self.cmake_dir / "build"),
            "CMAKE_CURRENT_LIST_DIR": str(self.cmake_dir),
            "PROJECT_SOURCE_DIR": str(self.repo_root),
            "PROJECT_BINARY_DIR": str(self.repo_root / "build"),
        }
        self.lists = {}
        self.fetchcontent_projects = {}

    def set_project_name(self, name: str):
        """Set project name from a `project()` command.

        Args:
            name: Project name string.
        """
        self.project_name = name
        self.variables["CMAKE_PROJECT_NAME"] = name

    def declare_fetchcontent_project(self, project_name: str, git_repository: str = "", git_tag: str = ""):
        """Declare a FetchContent project and set up its variables.

        Args:
            project_name: Logical name of the project.
            git_repository: Optional backing repository URL (informational).
            git_tag: Optional version tag (informational).
        """
        project_name_lower = project_name.lower()
        deps_dir = self.cmake_dir / "_deps"
        project_source_dir = deps_dir / f"{project_name_lower}-src"
        project_binary_dir = deps_dir / f"{project_name_lower}-build"

        self.fetchcontent_projects[project_name_lower] = {
            "name": project_name,
            "git_repository": git_repository,
            "git_tag": git_tag,
            "source_dir": str(project_source_dir),
            "binary_dir": str(project_binary_dir),
        }

        self.variables[f"{project_name_lower}_SOURCE_DIR"] = str(project_source_dir)
        self.variables[f"{project_name_lower}_BINARY_DIR"] = str(project_binary_dir)
        project_name_upper = project_name.upper()
        self.variables[f"{project_name_upper}_SOURCE_DIR"] = str(project_source_dir)
        self.variables[f"{project_name_upper}_BINARY_DIR"] = str(project_binary_dir)

    def get_fetchcontent_project(self, project_name: str) -> Optional[dict]:
        """Get FetchContent project information.

        Args:
            project_name: Project name (case-insensitive).

        Returns:
            Dictionary with project info or None if not found.
        """
        return self.fetchcontent_projects.get(project_name.lower())

    def set_variable(self, name: str, value: str):
        """Set a scalar variable value.

        Args:
            name: Variable name.
            value: Variable value as string.
        """
        self.variables[name] = value

    def append_to_list(self, list_name: str, items: list):
        """Append items to a CMake list variable.

        Args:
            list_name: Variable name treated as a list.
            items: Items to append.
        """
        if list_name not in self.lists:
            self.lists[list_name] = []
        self.lists[list_name].extend(items)
        self.variables[list_name] = ";".join(self.lists[list_name])

    def set_list(self, list_name: str, items: list):
        """Set a CMake list variable value.

        Args:
            list_name: Variable name treated as a list.
            items: New list value.
        """
        self.lists[list_name] = items[:]
        self.variables[list_name] = ";".join(items)

    def get_list_items(self, list_name: str) -> list:
        """Return items for a list variable.

        Args:
            list_name: Name of the list variable.

        Returns:
            List of string items or an empty list if unset.
        """
        return self.lists.get(list_name, [])

    def expand_list_variable(self, var_name: str) -> list:
        """Expand a list variable into individual items.

        Args:
            var_name: Variable reference like "SOURCES" or "${SOURCES}".

        Returns:
            List of string items for the list variable.
        """
        clean_var_name = var_name.strip("${}")
        return self.get_list_items(clean_var_name)

    def glob_files(self, patterns: list) -> list:
        """Expand glob patterns to files relative to current CMake directory.

        Args:
            patterns: List of glob patterns (variables will be resolved first).

        Returns:
            List of file paths as strings (relative to the CMake file directory
            when possible).
        """
        files: list[str] = []
        for pattern in patterns:
            resolved_pattern = self.resolve(pattern)
            if not Path(resolved_pattern).is_absolute():
                resolved_pattern = str(self.cmake_dir / resolved_pattern)
            matching_files = glob.glob(resolved_pattern)
            for file_path in matching_files:
                try:
                    rel_path = Path(file_path).relative_to(self.cmake_dir)
                    files.append(str(rel_path))
                except ValueError:
                    files.append(file_path)
        return files

    def resolve(self, var_expr: str) -> str:
        """Resolve `${VAR}` occurrences within a string.

        Args:
            var_expr: Expression possibly containing `${VAR}` placeholders.

        Returns:
            String with variables resolved where values are known.
        """
        if var_expr.startswith("${") and var_expr.endswith("}"):
            var_name = var_expr[2:-1]
            resolved = self.variables.get(var_name)
            if resolved is None:
                resolved = os.environ.get(var_name)
            if resolved is None:
                return var_expr
            return resolved

        result = var_expr
        for match in CMAKE_VAR_PATTERN.finditer(var_expr):
            var_full = match.group(0)
            var_name = match.group(1)
            if var_name in self.variables:
                replacement = self.variables[var_name]
                result = result.replace(var_full, replacement)
            else:
                env_val = os.environ.get(var_name)
                if env_val is not None:
                    result = result.replace(var_full, env_val)

        if result != var_expr and CMAKE_VAR_PATTERN.search(result):
            self._resolve_depth += 1
            if self._resolve_depth < 10:
                resolved_result = self.resolve(result)
                self._resolve_depth -= 1
                return resolved_result
            self._resolve_depth -= 1
            logger.warning("Max recursion depth reached resolving: %s", var_expr)
        return result

