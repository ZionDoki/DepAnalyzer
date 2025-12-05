"""NPM package.json parser.

Parses package.json files to extract module information, dependencies,
and build configuration for Node.js projects.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from depanalyzer.graph import EdgeKind, NodeType
from depanalyzer.graph.models.identifiers import normalize_node_id
from depanalyzer.parsers.base import BaseParser, ConfigurationError
from depanalyzer.runtime.eventbus import Event, EventType

logger = logging.getLogger("depanalyzer.parsers.npm.config_parser")


class NpmParser(BaseParser):
    """Parser for npm package.json files.

    Extracts:
    - Package metadata (name, version, description)
    - Dependencies (dependencies, devDependencies, peerDependencies)
    - Entry points (main, module, exports)
    - Workspaces configuration (monorepo support)
    - Native addon indicators (binding.gyp, gypfile)
    """

    NAME = "npm_parser"
    ECOSYSTEM = "npm"

    def __init__(
        self,
        workspace_root: Path,
        graph_manager,
        eventbus,
        config: Optional[Any] = None,
        contract_registry: Optional[Any] = None,
    ) -> None:
        super().__init__(workspace_root, graph_manager, eventbus, config=config, contract_registry=contract_registry)
        self._last_parsed_dir: Optional[Path] = None

    def parse(self, target_path: Path) -> None:
        """Parse a package.json file.

        Args:
            target_path: Path to the package.json file.

        Raises:
            ConfigurationError: If the package.json is malformed.
        """
        logger.info("NpmParser: parsing %s", target_path)

        try:
            content = target_path.read_text(encoding="utf-8")
            pkg = json.loads(content)
        except json.JSONDecodeError as e:
            raise ConfigurationError(f"Invalid JSON in {target_path}: {e}") from e
        except OSError as e:
            raise ConfigurationError(f"Cannot read {target_path}: {e}") from e

        # Extract package info
        pkg_name = pkg.get("name", target_path.parent.name)
        pkg_version = pkg.get("version", "0.0.0")
        pkg_dir = target_path.parent

        # Store for discover_code_files()
        self._last_parsed_dir = pkg_dir

        # Create module node
        module_id = self._make_module_id(target_path)
        self._create_module_node(module_id, pkg, target_path)

        # Parse dependencies
        self._parse_dependencies(module_id, pkg, "dependencies")

        # Parse devDependencies if configured
        if self._should_include_dev_dependencies():
            self._parse_dependencies(module_id, pkg, "devDependencies", is_dev=True)

        # Parse peerDependencies (optional)
        if self._should_include_peer_dependencies():
            self._parse_dependencies(module_id, pkg, "peerDependencies", is_peer=True)

        # Parse workspaces (monorepo)
        self._parse_workspaces(module_id, pkg, pkg_dir)

        # Detect native addon
        if self._has_native_addon(pkg, pkg_dir):
            self._record_native_dirs(module_id, pkg_dir)

        # Publish parse event
        self._publish_parse_event(module_id, pkg, target_path)

        logger.info(
            "NpmParser: completed parsing %s (name=%s, version=%s)",
            target_path,
            pkg_name,
            pkg_version,
        )

    def _make_module_id(self, target_path: Path) -> str:
        """Generate module ID from package.json path.

        Args:
            target_path: Path to package.json.

        Returns:
            str: Normalized module ID.
        """
        # Use the directory containing package.json as the module path
        module_dir = target_path.parent
        return normalize_node_id(module_dir, self.workspace_root)

    def _create_module_node(
        self,
        module_id: str,
        pkg: Dict[str, Any],
        target_path: Path,
    ) -> None:
        """Create a MODULE node for the npm package.

        Args:
            module_id: Node ID for the module.
            pkg: Parsed package.json content.
            target_path: Path to package.json.
        """
        pkg_name = pkg.get("name", target_path.parent.name)
        pkg_version = pkg.get("version", "0.0.0")
        pkg_dir = target_path.parent

        # Extract entry points
        main_entry = pkg.get("main")
        module_entry = pkg.get("module")
        exports = pkg.get("exports")

        # Extract metadata
        description = pkg.get("description", "")
        license_str = pkg.get("license")
        repository = pkg.get("repository")
        if isinstance(repository, dict):
            repository = repository.get("url", "")

        self.add_node(
            module_id,
            NodeType.MODULE.value,
            parser_name=self.NAME,
            name=pkg_name,
            version=pkg_version,
            description=description,
            license=license_str,
            repository=repository,
            src_path=str(pkg_dir),
            config_path=str(target_path),
            ecosystem=self.ECOSYSTEM,
            origin="in_repo",
            # Entry points
            main=main_entry,
            module_entry=module_entry,
            exports=exports,
            # Workspaces
            workspaces=pkg.get("workspaces"),
        )

        logger.debug("NpmParser: created MODULE node %s", module_id)

    def _parse_dependencies(
        self,
        module_id: str,
        pkg: Dict[str, Any],
        dep_field: str,
        is_dev: bool = False,
        is_peer: bool = False,
    ) -> None:
        """Parse dependencies from package.json.

        Args:
            module_id: Parent module node ID.
            pkg: Parsed package.json content.
            dep_field: Field name (dependencies, devDependencies, etc.).
            is_dev: Whether these are dev dependencies.
            is_peer: Whether these are peer dependencies.
        """
        deps = pkg.get(dep_field, {})
        if not isinstance(deps, dict):
            return

        for dep_name, version_spec in deps.items():
            self._create_dependency(
                module_id,
                dep_name,
                version_spec,
                is_dev=is_dev,
                is_peer=is_peer,
            )

    def _create_dependency(
        self,
        module_id: str,
        dep_name: str,
        version_spec: str,
        is_dev: bool = False,
        is_peer: bool = False,
    ) -> None:
        """Create an EXTERNAL_DEP node and edge for a dependency.

        Args:
            module_id: Parent module node ID.
            dep_name: Dependency package name.
            version_spec: Version specification string.
            is_dev: Whether this is a dev dependency.
            is_peer: Whether this is a peer dependency.
        """
        # Determine dependency type for ecosystem routing
        # Local file dependencies use "file:" prefix
        if version_spec.startswith("file:"):
            dep_ecosystem = "npm"
            dep_type = "local"
        elif version_spec.startswith("git") or "github" in version_spec:
            dep_ecosystem = "npm"
            dep_type = "git"
        else:
            dep_ecosystem = "npm"
            dep_type = "registry"

        # Create external dependency node ID
        dep_id = f"//external:npm/{dep_name}"

        # Create or update the dependency node
        if not self.graph_manager.has_node(dep_id):
            self.add_node(
                dep_id,
                NodeType.EXTERNAL_DEP.value,
                parser_name=self.NAME,
                name=dep_name,
                version=version_spec,
                ecosystem=dep_ecosystem,
                dep_type=dep_type,
                is_dev=is_dev,
                is_peer=is_peer,
                origin="external",
            )

        # Create dependency edge
        edge_attrs = {
            "parser_name": self.NAME,
            "version_spec": version_spec,
            "dep_type": dep_type,
        }
        if is_dev:
            edge_attrs["scope"] = "dev"
        if is_peer:
            edge_attrs["scope"] = "peer"

        self.add_edge(
            module_id,
            dep_id,
            EdgeKind.DEPENDS_ON.value,
            **edge_attrs,
        )

        logger.debug(
            "NpmParser: created dependency %s -> %s (%s)",
            module_id,
            dep_id,
            version_spec,
        )

    def _parse_workspaces(
        self,
        module_id: str,
        pkg: Dict[str, Any],
        pkg_dir: Path,
    ) -> None:
        """Parse workspaces configuration for monorepo support.

        Args:
            module_id: Root module node ID.
            pkg: Parsed package.json content.
            pkg_dir: Directory containing package.json.
        """
        workspaces = pkg.get("workspaces")
        if not workspaces:
            return

        # Workspaces can be a list or an object with "packages" key
        if isinstance(workspaces, dict):
            workspace_patterns = workspaces.get("packages", [])
        elif isinstance(workspaces, list):
            workspace_patterns = workspaces
        else:
            return

        if not workspace_patterns:
            return

        # Store workspace patterns on the module node for later linking
        self.graph_manager.update_node_attribute(
            module_id,
            "workspace_patterns",
            workspace_patterns,
        )

        logger.debug(
            "NpmParser: recorded %d workspace pattern(s) for %s",
            len(workspace_patterns),
            module_id,
        )

    def _has_native_addon(self, pkg: Dict[str, Any], pkg_dir: Path) -> bool:
        """Check if the package has native addon indicators.

        Args:
            pkg: Parsed package.json content.
            pkg_dir: Directory containing package.json.

        Returns:
            bool: True if native addon indicators are found.
        """
        # Check gypfile field
        if pkg.get("gypfile"):
            return True

        # Check for binding.gyp file
        binding_gyp = pkg_dir / "binding.gyp"
        if binding_gyp.exists():
            return True

        # Check for CMakeLists.txt (cmake-js)
        cmake_lists = pkg_dir / "CMakeLists.txt"
        if cmake_lists.exists():
            return True

        return False

    def _record_native_dirs(self, module_id: str, pkg_dir: Path) -> None:
        """Record native addon directories for CMake bridging.

        Args:
            module_id: Module node ID.
            pkg_dir: Directory containing package.json.
        """
        native_dirs = [str(pkg_dir)]

        # Check for common native source directories
        for subdir in ["src", "native", "binding"]:
            native_path = pkg_dir / subdir
            if native_path.is_dir():
                native_dirs.append(str(native_path))

        self.graph_manager.update_node_attribute(
            module_id,
            "native_dirs",
            native_dirs,
        )
        self.graph_manager.update_node_attribute(
            module_id,
            "has_native_addon",
            True,
        )

        logger.debug(
            "NpmParser: recorded native_dirs for %s: %s",
            module_id,
            native_dirs,
        )

    def _should_include_dev_dependencies(self) -> bool:
        """Check if devDependencies should be included.

        Returns:
            bool: True if devDependencies should be parsed.
        """
        if self.config:
            return getattr(self.config, "include_dev_dependencies", False)
        return False

    def _should_include_peer_dependencies(self) -> bool:
        """Check if peerDependencies should be included.

        Returns:
            bool: True if peerDependencies should be parsed.
        """
        if self.config:
            return getattr(self.config, "include_peer_dependencies", False)
        return False

    def _publish_parse_event(
        self,
        module_id: str,
        pkg: Dict[str, Any],
        target_path: Path,
    ) -> None:
        """Publish parse completion event.

        Args:
            module_id: Parsed module node ID.
            pkg: Parsed package.json content.
            target_path: Path to package.json.
        """
        event = Event(
            event_type=EventType.MODULE_PARSED,
            source=self.NAME,
            data={
                "module_id": module_id,
                "package_name": pkg.get("name"),
                "package_version": pkg.get("version"),
                "target_path": str(target_path),
                "ecosystem": self.ECOSYSTEM,
            },
        )
        self.publish_parse_event(event)

    def discover_code_files(self) -> List[Path]:
        """Discover JavaScript/TypeScript source files.

        Returns:
            List[Path]: List of source code files to parse.
        """
        if self._last_parsed_dir is None:
            return []

        from fnmatch import fnmatch
        from depanalyzer.parsers.npm.code_parser import NpmCodeParser

        code_files: List[Path] = []
        seen: Set[Path] = set()

        # File extensions to search for (derived from CODE_GLOBS)
        extensions = [".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts"]

        try:
            for ext in extensions:
                pattern = f"*{ext}"
                for file_path in self._last_parsed_dir.rglob(pattern):
                    if not file_path.is_file():
                        continue

                    # Check ignore patterns
                    rel_path = str(file_path.relative_to(self._last_parsed_dir))
                    # Normalize path separators for fnmatch
                    rel_path_normalized = rel_path.replace("\\", "/")

                    skip = False
                    for ignore in NpmCodeParser.IGNORE_PATTERNS:
                        if fnmatch(rel_path_normalized, ignore):
                            skip = True
                            break

                    if skip:
                        continue

                    resolved = file_path.resolve()
                    if resolved not in seen:
                        seen.add(resolved)
                        code_files.append(resolved)

        except (OSError, ValueError) as exc:
            logger.debug("Failed scanning %s: %s", self._last_parsed_dir, exc)

        return code_files
