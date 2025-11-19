"""HVigor configuration parser producing unified graph structures."""

# Parser must continue operating even when individual config files are malformed.


import logging
from pathlib import Path
from typing import Any, Dict

import json5

from depanalyzer.parsers.base import BaseParser, DependencySpec
from depanalyzer.graph.manager import GraphManager, NodeType, EdgeKind
from depanalyzer.graph.schema import NodeSpec, EdgeSpec
from depanalyzer.runtime.eventbus import Event, EventType
from depanalyzer.graph.contract import BuildInterfaceContract, ContractType
from depanalyzer.graph.contract_registry import ContractRegistry

logger = logging.getLogger("depanalyzer.parsers.hvigor.config")


class HvigorParser(BaseParser):
    """
    Parses all critical configuration files for ArkTS/HVigor projects, creating
    all nodes and edges with a unified format.

    In the new architecture, cross-domain associations are handled by hooks.
    This parser focuses on extracting facts from configuration files and
    publishing events for hook consumption.
    """

    NAME = "hvigor"

    def __init__(
        self,
        workspace_root: Path,
        graph_manager: GraphManager,
        eventbus,
        config: Any | None = None,
    ) -> None:
        """Initialize Hvigor configuration parser.

        Args:
            workspace_root: Workspace root path.
            graph_manager: Transaction graph manager.
            eventbus: Event bus for publishing parse events.
        Returns:
            None.
        """
        super().__init__(workspace_root, graph_manager, eventbus, config=config)
        self._last_config_result: Dict[str, Any] = {}

    def parse(self, target_path: Path) -> None:
        """Parse a single config file and update the graph.

        Args:
            target_path: Config file path.
        """
        parsed_data = self._parse_single_config(target_path)
        self._last_config_result = parsed_data
        self._process_config_result(parsed_data)

    def _parse_single_config(self, file_path: Path) -> Dict[str, Any]:
        """Parse a single JSON5 config file and classify its content."""
        result = {"file": file_path, "type": "unknown"}
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                config = json5.load(f)

            if file_path.name == "build-profile.json5":
                result["type"] = "build_profile"
                result["modules"] = config.get("modules", [])
            elif file_path.name == "oh-package.json5":
                result["type"] = "package_dependencies"

                # Check both dependencies and devDependencies for native libraries
                all_deps = {
                    **config.get("dependencies", {}),
                    **config.get("devDependencies", {}),
                }
                # Store dependency list with version info (including file: dependencies)
                result["dependencies"] = [
                    {"name": name, "version": version}
                    for name, version in all_deps.items()
                ]

                native_deps = {
                    name: path.replace("file:", "", 1)
                    for name, path in all_deps.items()
                    if isinstance(path, str) and path.startswith("file:")
                }
                if native_deps:
                    result["native_dependencies"] = native_deps
                if "types" in config:
                    result["bridge_dts"] = config.get("types")

            elif file_path.name == "module.json5":
                result["type"] = "module_config"
                module_cfg = config.get("module", {}) or {}
                result["module_name"] = module_cfg.get("name", file_path.parent.name)
                result["module_type"] = module_cfg.get("type")
            elif file_path.name == "oh-package-lock.json5":
                result["type"] = "lock_file"
                # ohpm lock v3 uses packages/specifiers structure
                deps_list = []

                # Legacy structure (lockfileVersion < 3: dependencies)
                deps_legacy = config.get("dependencies", {}) or {}
                for name, info in deps_legacy.items():
                    ver = info.get("version") if isinstance(info, dict) else ""
                    deps_list.append(
                        {"name": name, "version": ver, "registry_type": "ohpm"}
                    )

                # New structure (lockfileVersion 3: packages)
                packages = config.get("packages", {}) or {}
                for info in packages.values():
                    if not isinstance(info, dict):
                        continue

                    name = info.get("name")
                    ver = info.get("version")
                    registry_type = info.get("registryType", "ohpm")

                    if name:
                        deps_list.append(
                            {
                                "name": name,
                                "version": ver,
                                "registry_type": registry_type,
                            }
                        )

                result["external_dependencies"] = deps_list

            elif file_path.name == "hvigor-config.json5":
                result["type"] = "hvigor_config"
                # Record only; concrete discovery handled by discover_dependencies
                result["plugins"] = config.get("plugins")
        except (OSError, ValueError) as e:
            logger.warning("Could not parse build config %s: %s", file_path, e)
        return result

    def _process_config_result(self, result: Dict[str, Any]):
        """Processes a single parsed config file, creating nodes and publishing events."""
        config_file: Path = result.get("file")
        if not config_file:
            return

        rel_path_str = str(config_file.relative_to(self.workspace_root))

        # Create config file node using the unified schema
        config_spec = NodeSpec(
            id=rel_path_str,
            type=NodeType.CONFIG,
            label=rel_path_str,
            src_path=str(config_file.resolve()),
            name=config_file.name,
            parser_name=self.NAME,
        )
        self.graph_manager.add_node_spec(config_spec)

        result_type = result.get("type")

        if result_type == "build_profile":
            self._process_build_profile(result, rel_path_str)
        elif result_type == "module_config":
            self._process_module_config(result, rel_path_str)
        elif result_type == "package_dependencies":
            self._process_package_dependencies(result, rel_path_str)
        elif result_type == "lock_file":
            self._process_lock_file(result, rel_path_str)
        elif result_type == "hvigor_config":
            self._process_hvigor_config(result, rel_path_str)

    def discover_code_files(self) -> list[Path]:
        """Discover ArkTS/TypeScript/JavaScript source files from config context.

        Args:
            None.

        Returns:
            list[Path]: List of source code files to feed into code parsing.
        """
        if not self._last_config_result:
            return []

        result_type = self._last_config_result.get("type")
        config_file: Path = self._last_config_result.get("file")
        if not isinstance(config_file, Path):
            return []

        code_files: list[Path] = []

        try:
            if result_type == "build_profile":
                for module_item in self._last_config_result.get("modules", []):
                    if isinstance(module_item, dict):
                        module_name = module_item.get("name")
                    else:
                        module_name = module_item

                    if not module_name or not isinstance(module_name, str):
                        continue

                    module_root = (self.workspace_root / module_name).resolve()
                    code_files.extend(self._discover_module_code_files(module_root))

            elif result_type == "module_config":
                module_root = config_file.parent.parent.parent.resolve()
                code_files.extend(self._discover_module_code_files(module_root))
        except (OSError, ValueError) as exc:
            logger.warning("Failed to discover Hvigor code files: %s", exc)
            return []

        # Deduplicate and keep only existing files
        unique_files: list[Path] = []
        seen: set[Path] = set()
        for path in code_files:
            try:
                resolved = path.resolve()
            except OSError:
                continue

            if not resolved.is_file():
                continue

            if resolved in seen:
                continue

            seen.add(resolved)
            unique_files.append(resolved)

        return unique_files

    def _discover_module_code_files(self, module_root: Path) -> list[Path]:
        """Discover ArkTS/TS/JS files under a module root.

        Args:
            module_root: Module root directory path.
        Returns:
            list[Path]: Source files discovered under the module root.
        """
        if not module_root.is_dir():
            return []

        code_files: list[Path] = []
        try:
            for pattern in ("**/*.ets", "**/*.ts", "**/*.js"):
                for file_path in module_root.rglob(pattern):
                    if file_path.is_file():
                        code_files.append(file_path)
        except (OSError, ValueError) as exc:
            logger.warning(
                "Error while scanning module root %s for source files: %s",
                module_root,
                exc,
            )
            return []

        return code_files

    def _process_build_profile(self, result: Dict[str, Any], config_file_id: str):
        """Process build-profile.json5 file."""
        for module_item in result.get("modules", []):
            module_name = (
                module_item.get("name")
                if isinstance(module_item, dict)
                else module_item
            )
            if not module_name or not isinstance(module_name, str):
                continue

            module_id = f"module:{module_name}"
            module_root = Path(self.workspace_root) / module_name

            module_spec = NodeSpec(
                id=module_id,
                type=NodeType.MODULE,
                label=module_id,
                src_path=str(module_root.resolve()),
                name=module_name,
                parser_name=self.NAME,
                confidence=1.0,
                attrs={
                    "origin": "in_repo",
                    "provenance": "build_profile",
                    "declared_via": "build-profile.json5",
                },
            )
            self.graph_manager.add_node_spec(module_spec)

            edge_spec = EdgeSpec(
                source=module_id,
                target=config_file_id,
                kind=EdgeKind.DEFINED_BY,
                parser_name=self.NAME,
            )
            self.graph_manager.add_edge_spec(edge_spec)

            # Publish event for hooks
            event = Event(
                event_type=EventType.MODULE_PARSED,
                source=self.NAME,
                data={
                    "module_id": module_id,
                    "module_name": module_name,
                    "module_root": str(module_root.relative_to(self.workspace_root)),
                    "declared_via": "build-profile.json5",
                },
            )
            self.publish_parse_event(event)

    def _process_module_config(self, result: Dict[str, Any], config_file_id: str):
        """Process module.json5 file."""
        module_name = result.get("module_name")
        if not module_name:
            return

        config_file = result.get("file")
        module_id = f"module:{module_name}"
        module_root = config_file.parent.parent.parent

        module_type = result.get("module_type")

        module_spec = NodeSpec(
            id=module_id,
            type=NodeType.MODULE,
            label=module_id,
            src_path=str(module_root.resolve()),
            name=module_name,
            parser_name=self.NAME,
            confidence=1.0,
            attrs={
                "origin": "in_repo",
                "provenance": "module_config",
                "declared_via": "module.json5",
                "module_type": module_type,
            },
        )
        self.graph_manager.add_node_spec(module_spec)

        edge_spec = EdgeSpec(
            source=module_id,
            target=config_file_id,
            kind=EdgeKind.DEFINED_BY,
            parser_name=self.NAME,
        )
        self.graph_manager.add_edge_spec(edge_spec)

        # Publish event for hooks
        event = Event(
            event_type=EventType.MODULE_PARSED,
            source=self.NAME,
            data={
                "module_id": module_id,
                "module_name": module_name,
                "module_root": str(module_root.relative_to(self.workspace_root)),
                "declared_via": "module.json5",
            },
        )
        self.publish_parse_event(event)

    def _process_package_dependencies(
        self, result: Dict[str, Any], config_file_id: str
    ):
        """Process oh-package.json5 dependencies."""
        config_file = result.get("file")

        # Determine owning module from config file path
        # oh-package.json5 files can be at:
        # - project root: <root>/oh-package.json5 -> no owning module
        # - module level: <root>/<module>/oh-package.json5 -> module:<module>
        owning_module_id = None
        try:
            rel_to_root = config_file.relative_to(self.workspace_root)
            parts = rel_to_root.parts
            if len(parts) == 1:
                # Root level oh-package.json5
                owning_module_id = None
            elif len(parts) >= 2:
                # Module level: parts[0] is the module name
                module_name = parts[0]
                owning_module_id = f"module:{module_name}"

                # Ensure owning module node exists with correct type.
                existing = self.graph_manager.get_node(owning_module_id)
                if existing is None:
                    module_root = (self.workspace_root / module_name).resolve()
                    module_spec = NodeSpec(
                        id=owning_module_id,
                        type=NodeType.MODULE,
                        label=owning_module_id,
                        src_path=str(module_root),
                        name=module_name,
                        parser_name=self.NAME,
                        confidence=0.9,
                        attrs={
                            "origin": "in_repo",
                            "provenance": "package_config",
                            "declared_via": "oh-package.json5",
                        },
                    )
                    self.graph_manager.add_node_spec(module_spec)
                elif existing.get("type") != NodeType.MODULE.value:
                    # Upgrade placeholder/unknown nodes to proper module type
                    self.graph_manager.update_node_attribute(
                        owning_module_id, "type", NodeType.MODULE
                    )

                # Link module to its config file
                edge_spec = EdgeSpec(
                    source=owning_module_id,
                    target=config_file_id,
                    kind=EdgeKind.DEFINED_BY,
                    parser_name=self.NAME,
                )
                self.graph_manager.add_edge_spec(edge_spec)
                logger.debug(
                    "Linked module %s to config %s",
                    owning_module_id,
                    config_file_id,
                )
        except ValueError:
            pass

        # Process regular dependencies
        deps = result.get("dependencies", [])
        for dep in deps:
            if isinstance(dep, dict):
                dep_name = dep.get("name")
                dep_version = dep.get("version")
            else:
                dep_name = dep
                dep_version = None

            if not dep_name:
                continue

            # Check if this is a local (file:) dependency
            is_local = isinstance(dep_version, str) and dep_version.startswith("file:")

            if is_local:
                # Local dependency - reference to another module or local package
                # Extract the target module name from the dependency name (e.g., @sj/ffmpeg -> ffmpeg)
                target_module_name = (
                    dep_name.split("/")[-1] if "/" in dep_name else dep_name
                )
                target_module_id = f"module:{target_module_name}"

                # Ensure target module node exists so it does not stay as type=unknown
                target_existing = self.graph_manager.get_node(target_module_id)
                if target_existing is None:
                    target_root = (self.workspace_root / target_module_name).resolve()
                    target_spec = NodeSpec(
                        id=target_module_id,
                        type=NodeType.MODULE,
                        label=target_module_id,
                        src_path=str(target_root),
                        name=target_module_name,
                        parser_name=self.NAME,
                        confidence=0.8,
                        attrs={
                            "origin": "in_repo",
                            "provenance": "package_dependencies",
                            "declared_via": "oh-package.json5",
                        },
                    )
                    self.graph_manager.add_node_spec(target_spec)
                elif target_existing.get("type") != NodeType.MODULE.value:
                    self.graph_manager.update_node_attribute(
                        target_module_id, "type", NodeType.MODULE
                    )

                # Create edge from owning module to target module
                if owning_module_id:
                    edge_spec = EdgeSpec(
                        source=owning_module_id,
                        target=target_module_id,
                        kind=EdgeKind.DEPENDS_ON,
                        parser_name=self.NAME,
                    )
                    self.graph_manager.add_edge_spec(edge_spec)
                    logger.debug(
                        "Created local dependency edge: %s -> %s",
                        owning_module_id,
                        target_module_id,
                    )
            else:
                # External dependency
                if dep_version and dep_version != "":
                    lib_id = f"ext_lib:{dep_name}@{dep_version}"
                else:
                    lib_id = f"ext_lib:{dep_name}"

                # Do not set a fake src_path to avoid bogus path normalization.
                # External libraries are modeled as logical nodes only.
                lib_spec = NodeSpec(
                    id=lib_id,
                    type=NodeType.EXTERNAL_LIBRARY,
                    label=lib_id,
                    name=dep_name,
                    parser_name=self.NAME,
                    confidence=0.9,
                    attrs={
                        "version": str(dep_version) if dep_version else None,
                        "origin": "external",
                        "provenance": "ohpm_package",
                        "declared_via": "oh-package.json5",
                        "ecosystem": "hvigor",
                    },
                )
                self.graph_manager.add_node_spec(lib_spec)

                # Create edge from owning module to external library
                if owning_module_id:
                    edge_spec = EdgeSpec(
                        source=owning_module_id,
                        target=lib_id,
                        kind=EdgeKind.DEPENDS_ON,
                        parser_name=self.NAME,
                    )
                    self.graph_manager.add_edge_spec(edge_spec)
                    logger.debug(
                        "Created external dependency edge: %s -> %s",
                        owning_module_id,
                        lib_id,
                    )

                # When a sibling oh-package-lock.json5 exists, that lock file
                # provides the authoritative version information for OHPM
                # dependencies. In that case we rely on _process_lock_file()
                # to emit DEPENDENCY_DISCOVERED events, and skip emitting an
                # additional event here to avoid duplicate or conflicting
                # DependencySpec instances (e.g. semver ranges vs. locked
                # concrete versions).
                emit_event = True
                if isinstance(config_file, Path):
                    lock_path = config_file.parent / "oh-package-lock.json5"
                    if lock_path.exists():
                        emit_event = False

                if emit_event:
                    spec = DependencySpec(
                        name=dep_name,
                        version=str(dep_version) if dep_version else "",
                        ecosystem="hvigor",
                        metadata={
                            "parser_name": self.NAME,
                            "depth": 0,
                        },
                    )
                    event = Event(
                        event_type=EventType.DEPENDENCY_DISCOVERED,
                        source=self.NAME,
                        data={
                            "spec": spec,
                            "source_file": str(config_file),
                        },
                    )
                    self.publish_parse_event(event)

        # Process native dependencies (for native bridge detection and shared libraries).
        cfg = getattr(self, "config", None)
        enable_native = True
        if cfg is not None:
            enable_native = bool(getattr(cfg, "enable_native_dependencies", True))

        if not enable_native:
            logger.debug("HvigorParser native dependency processing disabled by config")
            return

        # Native dependency handling and contract registration.
        native_deps = result.get("native_dependencies", {})
        for lib_name, rel_type_path in native_deps.items():
            try:
                type_dir = (config_file.parent / rel_type_path).resolve()
                native_pkg_json_path = type_dir / "oh-package.json5"
                if not native_pkg_json_path.exists():
                    continue

                with native_pkg_json_path.open("r", encoding="utf-8") as f:
                    native_pkg_config = json5.load(f)

                dts_rel_path = native_pkg_config.get("types")
                if not dts_rel_path:
                    continue

                dts_abs_path = (native_pkg_json_path.parent / dts_rel_path).resolve()
                dts_id = str(dts_abs_path.relative_to(self.workspace_root))

                # Record native_dir on the owning module for linker usage.
                if owning_module_id:
                    module_attrs = self.graph_manager.get_node(owning_module_id) or {}
                    native_dirs = module_attrs.get("native_dirs") or []
                    if isinstance(native_dirs, list):
                        if str(type_dir) not in native_dirs:
                            native_dirs.append(str(type_dir))
                    else:
                        native_dirs = [str(type_dir)]
                    self.graph_manager.update_node_attribute(
                        owning_module_id,
                        "native_dirs",
                        native_dirs,
                    )

                # Create .d.ts node describing the native interface surface.
                dts_spec = NodeSpec(
                    id=dts_id,
                    type=NodeType.CODE,
                    label=dts_id,
                    src_path=str(dts_abs_path),
                    name=Path(dts_id).name,
                    parser_name=self.NAME,
                    confidence=0.9,
                    attrs={
                        "origin": "in_repo",
                        "provenance": "ohpm_native_types",
                        "declared_via": "oh-package.json5",
                    },
                )
                self.graph_manager.add_node_spec(dts_spec)

                logger.info("Found native bridge: %s -> %s", lib_name, dts_id)

                # Register consumer-side contracts and create expected shared
                # library nodes under the owning module. Hvigor always links
                # native dependencies as shared libraries.
                try:
                    registry = ContractRegistry()

                    # Typically in module/libs/{abi}/libname.so. Use multiple
                    # ABI options for broader matching.
                    for abi in ["arm64-v8a", "armeabi-v7a", "x86_64"]:
                        expected_artifact_path = (
                            config_file.parent / "libs" / abi / f"lib{lib_name}.so"
                        )
                        expected_artifact_id = self.graph_manager.normalize_path(
                            expected_artifact_path
                        )

                        # Ensure a consumer artifact node exists so that
                        # modules and link strategies have a stable anchor.
                        if not self.graph_manager.has_node(expected_artifact_id):
                            artifact_spec = NodeSpec(
                                id=expected_artifact_id,
                                type=NodeType.ARTIFACT,
                                label=str(expected_artifact_path),
                                src_path=str(expected_artifact_path),
                                name=f"lib{lib_name}.so",
                                parser_name=self.NAME,
                                confidence=0.9,
                                attrs={
                                    "origin": "in_repo",
                                    "provenance": "hvigor_native_dep",
                                    "declared_via": "oh-package.json5",
                                    "ecosystem": "hvigor",
                                },
                            )
                            self.graph_manager.add_node_spec(artifact_spec)

                        # Connect owning module -> consumer artifact to model
                        # the build-time dependency on the shared library.
                        if owning_module_id:
                            edge_spec = EdgeSpec(
                                source=owning_module_id,
                                target=expected_artifact_id,
                                kind=EdgeKind.DEPENDS_ON,
                                parser_name=self.NAME,
                            )
                            self.graph_manager.add_edge_spec(edge_spec)

                        # Create consumer contract for cross-language matching.
                        contract = BuildInterfaceContract(
                            provider_artifact="",  # To be matched in JOIN phase
                            consumer_artifact=expected_artifact_id,
                            artifact_name=f"lib{lib_name}.so",
                            contract_type=ContractType.ARTIFACT_NAME,
                            confidence=0.0,  # Will be set during matching
                            evidence=[
                                f"hvigor_dependency:{lib_name}",
                                f"native_dir:{type_dir}",
                                f"interface:{dts_id}",
                            ],
                            interface_files=[
                                self.graph_manager.normalize_path(dts_abs_path)
                            ],
                            metadata={
                                "lib_name": lib_name,
                                "abi": abi,
                                "native_dir": str(type_dir),
                            },
                        )
                        registry.register(contract)
                        logger.debug(
                            "Registered consumer contract: %s (abi=%s)",
                            lib_name,
                            abi,
                        )
                except Exception as contract_err:  # noqa: BLE001
                    logger.warning(
                        "Failed to register native dependency contract for %s: %s",
                        lib_name,
                        contract_err,
                    )

            except (OSError, ValueError) as e:
                logger.warning(
                    "Could not process native dependency bridge for %s: %s",
                    lib_name,
                    e,
                )

    def _process_lock_file(self, result: Dict[str, Any], _config_file_id: str):
        """Process oh-package-lock.json5 file."""
        config_file = result.get("file")

        # Determine owning module from lock file path (same logic as oh-package.json5)
        owning_module_id = None
        try:
            rel_to_root = config_file.relative_to(self.workspace_root)
            parts = rel_to_root.parts
            if len(parts) >= 2:
                module_name = parts[0]
                owning_module_id = f"module:{module_name}"
        except ValueError:
            pass

        for dep in result.get("external_dependencies", []):
            lib_name = dep.get("name")
            if not lib_name:
                continue

            lib_version = dep.get("version")
            registry_type = dep.get("registry_type", "ohpm")

            # Skip local dependencies - they're handled by oh-package.json5
            if registry_type == "local":
                continue

            lib_id = (
                f"ext_lib:{lib_name}@{lib_version}"
                if lib_version
                else f"ext_lib:{lib_name}"
            )

            self.add_node(
                node_id=lib_id,
                node_type=NodeType.EXTERNAL_LIBRARY,
                label=lib_id,
                src_path="N/A",
                name=lib_name,
                version=lib_version,
                parser_name=self.NAME,
                origin="external",
                provenance="ohpm_lock",
                declared_via="oh-package-lock.json5",
                confidence=1.0,
                ecosystem="hvigor",
            )

            # Create edge from owning module to external library
            if owning_module_id:
                self.add_edge(
                    source=owning_module_id,
                    target=lib_id,
                    edge_kind=EdgeKind.DEPENDS_ON,
                    parser_name=self.NAME,
                )
                logger.debug(
                    "Created lock file dependency edge: %s -> %s",
                    owning_module_id,
                    lib_id,
                )

            # Publish dependency event
            spec = DependencySpec(
                name=lib_name,
                version=str(lib_version) if lib_version else "",
                ecosystem="hvigor",
                metadata={
                    "parser_name": self.NAME,
                    "depth": 0,
                    "registry_type": registry_type,
                },
            )
            event = Event(
                event_type=EventType.DEPENDENCY_DISCOVERED,
                source=self.NAME,
                data={
                    "spec": spec,
                    "source_file": str(result.get("file")),
                },
            )
            self.publish_parse_event(event)

    def _process_hvigor_config(self, result: Dict[str, Any], _config_file_id: str):
        """Process hvigor-config.json5 file."""
        plugins = result.get("plugins")
        if not plugins:
            return

        # Publish event about hvigor plugins
        event = Event(
            event_type=EventType.TARGET_PARSED,
            source=self.NAME,
            data={
                "config_type": "hvigor_config",
                "plugins": plugins,
                "config_file": str(result.get("file")),
            },
        )
        self.publish_parse_event(event)
