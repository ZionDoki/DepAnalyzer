"""Package command handler for find_package and FetchContent commands."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from parsers.cpp.cmake.commands.base import CommandHandler
from parsers.cpp.cmake.tokens import clean_token
from parsers.cpp.cmake.variables import CMakeVariableResolver
from utils.graph import GraphManager

log = logging.getLogger("depanalyzer.parsers.cpp.cmake.commands.packages")


class PackagesCommandHandler(CommandHandler):
    """Handler for package management commands (find_package, FetchContent_*)."""

    def can_handle(self, command_name: str) -> bool:
        """Check if this handler can process the given command.

        Args:
            command_name: The lowercase command name.

        Returns:
            True if command is related to packages or FetchContent.
        """
        return command_name in {
            "find_package",
            "fetchcontent_declare",
            "fetchcontent_makeavailable",
        }

    def handle(
        self,
        command_name: str,
        args: List[str],
        file_path: Path,
        shared_graph: GraphManager,
        variable_resolver: CMakeVariableResolver,
    ) -> bool:
        """Handle package management commands.

        Args:
            command_name: Command name.
            args: Command arguments.
            file_path: CMakeLists.txt path.
            shared_graph: Graph manager.
            variable_resolver: Variable resolver.

        Returns:
            True if the command was successfully processed.
        """
        if command_name == "find_package":
            return self._handle_find_package(args, file_path, shared_graph)
        elif command_name == "fetchcontent_declare":
            return self._handle_fetchcontent_declare(args, variable_resolver)
        elif command_name == "fetchcontent_makeavailable":
            return self._handle_fetchcontent_makeavailable(
                args, variable_resolver, shared_graph
            )
        return False

    def _handle_find_package(
        self, args: List[str], file_path: Path, shared_graph: GraphManager
    ) -> bool:
        """Handle find_package() command.

        Args:
            args: Command arguments.
            file_path: CMakeLists.txt path.
            shared_graph: Graph manager.

        Returns:
            True if handled successfully.
        """
        if not args:
            return False

        pkg = clean_token(args[0])
        if not pkg:
            return False

        required = any(str(a).upper() == "REQUIRED" for a in args[1:])
        version = None

        for i, tok in enumerate(args[1:], start=1):
            if str(tok).upper() == "VERSION" and i + 1 < len(args):
                version = clean_token(args[i + 1])
                break

        lib_id = f"ext_lib:{pkg}{('@' + version) if version else ''}"
        lib_v = shared_graph.create_vertex(
            lib_id,
            parser_name=self.parser_name,
            type="external_library",
            id=lib_id,
            origin="external",
            provenance="find_package",
            declared_via="find_package",
            confidence=(1.0 if required else 0.8),
            name=pkg,
            version=version,
        )
        shared_graph.add_node(lib_v)
        return True

    def _handle_fetchcontent_declare(
        self, args: List[str], variable_resolver: CMakeVariableResolver
    ) -> bool:
        """Handle FetchContent_Declare() command.

        Args:
            args: Command arguments.
            variable_resolver: Variable resolver.

        Returns:
            True if handled successfully.
        """
        if not args:
            return False

        project_name = clean_token(args[0])
        log.debug("Found FetchContent_Declare for project: %s", project_name)

        if not project_name:
            return False

        git_repository = ""
        git_tag = ""

        i = 1
        while i < len(args) - 1:
            arg = args[i].upper()
            if arg == "GIT_REPOSITORY" and i + 1 < len(args):
                git_repository = clean_token(args[i + 1])
                i += 2
            elif arg == "GIT_TAG" and i + 1 < len(args):
                git_tag = clean_token(args[i + 1])
                i += 2
            else:
                i += 1

        log.debug(
            "Declaring FetchContent project: %s (repo: %s, tag: %s)",
            project_name,
            git_repository,
            git_tag,
        )

        variable_resolver.declare_fetchcontent_project(
            project_name, git_repository, git_tag
        )
        return True

    def _handle_fetchcontent_makeavailable(
        self, args: List[str], variable_resolver: CMakeVariableResolver, shared_graph: GraphManager
    ) -> bool:
        """Handle FetchContent_MakeAvailable() command.

        Creates external_library nodes for each FetchContent project.

        Args:
            args: Command arguments (project names).
            variable_resolver: Variable resolver.
            shared_graph: Graph manager.

        Returns:
            True if handled successfully.
        """
        if not args:
            return False

        for arg in args:
            project_name = clean_token(arg)
            if not project_name:
                continue

            log.debug("Making available FetchContent project: %s", project_name)

            # Get project information from variable resolver
            proj_info = variable_resolver.get_fetchcontent_project(project_name)
            if proj_info:
                git_repo = proj_info.get("git_repository", "")
                git_tag = proj_info.get("git_tag", "")

                # Create external library node
                lib_id = f"ext_lib:{project_name}@fetchcontent"
                if git_tag:
                    lib_id = f"ext_lib:{project_name}@{git_tag}"

                lib_v = shared_graph.create_vertex(
                    lib_id,
                    parser_name=self.parser_name,
                    type="external_library",
                    id=lib_id,
                    origin="external",
                    provenance="fetchcontent",
                    declared_via="FetchContent_MakeAvailable",
                    confidence=1.0,
                    name=project_name,
                    version=git_tag if git_tag else "unknown",
                    git_repository=git_repo,
                    git_tag=git_tag,
                )
                shared_graph.add_node(lib_v)
                log.info("Created FetchContent node: %s (repo: %s, tag: %s)",
                         lib_id, git_repo, git_tag)
            else:
                # Create node even without Declare info (defensive)
                lib_id = f"ext_lib:{project_name}@fetchcontent"
                lib_v = shared_graph.create_vertex(
                    lib_id,
                    parser_name=self.parser_name,
                    type="external_library",
                    id=lib_id,
                    origin="external",
                    provenance="fetchcontent",
                    declared_via="FetchContent_MakeAvailable",
                    confidence=0.7,
                    name=project_name,
                )
                shared_graph.add_node(lib_v)
                log.warning(
                    "Created FetchContent node without Declare info: %s", project_name
                )

        return True
