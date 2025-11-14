"""HVigor configuration parser producing unified graph structures."""

import logging
from pathlib import Path
from typing import Any, Dict

import json5

from parsers.base import BaseParser
from core.dependency import DependencySpec, DependencyType
from core.schema import EdgeKind, NodeType
from runtime.eventbus import Event, EventType
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

    def parse(self, target_path: Path) -> None:
        """Parse a single config file and update the graph.

        Args:
            target_path: Config file path.
        """
        parsed_data = self._parse_single_config(target_path)
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
                # Store dependency list with version info
                result["dependencies"] = [
                    {"name": name, "version": version}
                    for name, version in all_deps.items()
                    if not (isinstance(version, str) and version.startswith("file:"))
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
                result["module_name"] = config.get("module", {}).get(
                    "name", file_path.parent.name
                )
            elif file_path.name == "oh-package-lock.json5":
                result["type"] = "lock_file"
                # ohpm lock v3 commonly uses packages/specifiers
                deps_list = []
                # Legacy structure (dependencies)
                deps_legacy = config.get("dependencies", {}) or {}
                for name, info in deps_legacy.items():
                    ver = info.get("version") if isinstance(info, dict) else ""
                    deps_list.append({"name": name, "version": ver})
                # New structure (packages)
                for _, info in (config.get("packages", {}) or {}).items():
                    name = info.get("name")
                    ver = info.get("version")
                    if name:
                        deps_list.append({"name": name, "version": ver})
                result["external_dependencies"] = deps_list

            elif file_path.name == "hvigor-config.json5":
                result["type"] = "hvigor_config"
                # Record only; concrete discovery handled by discover_dependencies
                result["plugins"] = config.get("plugins")
        except (OSError, ValueError) as e:
            logger.warning("Could not parse build config %s: %s", file_path, e)
        return result

    def _process_config_result(
        self, result: Dict[str, Any]
    ):
        """Processes a single parsed config file, creating nodes and publishing events."""
        config_file: Path = result.get("file")
        if not config_file:
            return

        rel_path_str = str(config_file.relative_to(self.workspace_root))

        # Create config file node
        self.add_node(
            node_id=rel_path_str,
            node_type=NodeType.CONFIG,
            label=rel_path_str,
            src_path=str(config_file.resolve()),
            name=config_file.name,
            parser_name=self.NAME,
        )

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

            self.add_node(
                node_id=module_id,
                node_type=NodeType.MODULE,
                label=module_id,
                src_path=str(module_root.resolve()),
                name=module_name,
                parser_name=self.NAME,
                origin="in_repo",
                provenance="build_profile",
                declared_via="build-profile.json5",
                confidence=1.0,
            )

            self.add_edge(
                source=module_id,
                target=config_file_id,
                edge_kind=EdgeKind.DEFINED_BY,
                parser_name=self.NAME,
            )

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

        self.add_node(
            node_id=module_id,
            node_type=NodeType.MODULE,
            label=module_id,
            src_path=str(module_root.resolve()),
            name=module_name,
            parser_name=self.NAME,
            origin="in_repo",
            provenance="module_config",
            declared_via="module.json5",
            confidence=1.0,
        )

        self.add_edge(
            source=module_id,
            target=config_file_id,
            edge_kind=EdgeKind.DEFINED_BY,
            parser_name=self.NAME,
        )

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

    def _process_package_dependencies(self, result: Dict[str, Any], config_file_id: str):
        """Process oh-package.json5 dependencies."""
        config_file = result.get("file")

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

            # Create external library node
            if dep_version and dep_version != "":
                lib_id = f"ext_lib:{dep_name}@{dep_version}"
            else:
                lib_id = f"ext_lib:{dep_name}"

            self.add_node(
                node_id=lib_id,
                node_type=NodeType.EXTERNAL_LIBRARY,
                label=lib_id,
                src_path="N/A",
                name=dep_name,
                version=str(dep_version) if dep_version else None,
                parser_name=self.NAME,
                origin="external",
                provenance="ohpm_package",
                declared_via="oh-package.json5",
                confidence=0.9,
            )

            # Publish dependency discovered event
            spec = DependencySpec(
                name=dep_name,
                version=str(dep_version) if dep_version else "",
                dependency_type=DependencyType.HVIGOR,
                parser_name=self.NAME,
                depth=0,
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

        # Process native dependencies (for native bridge detection)
        native_deps = result.get("native_dependencies", {})
        for lib_name, rel_type_path in native_deps.items():
            try:
                type_dir = (config_file.parent / rel_type_path).resolve()
                native_pkg_json_path = type_dir / "oh-package.json5"
                if native_pkg_json_path.exists():
                    with open(native_pkg_json_path, "r", encoding="utf-8") as f:
                        native_pkg_config = json5.load(f)
                    dts_rel_path = native_pkg_config.get("types")
                    if dts_rel_path:
                        dts_abs_path = (
                            native_pkg_json_path.parent / dts_rel_path
                        ).resolve()
                        dts_id = str(dts_abs_path.relative_to(self.workspace_root))

                        # Create .d.ts node
                        self.add_node(
                            node_id=dts_id,
                            node_type="code",
                            label=dts_id,
                            src_path=str(dts_abs_path),
                            name=Path(dts_id).name,
                            parser_name=self.NAME,
                            origin="in_repo",
                            provenance="ohpm_native_types",
                            declared_via="oh-package.json5",
                            confidence=0.9,
                        )

                        # Publish native bridge discovery event
                        event = Event(
                            event_type=EventType.HVIGOR_NATIVE_DIR_FOUND,
                            source=self.NAME,
                            data={
                                "lib_name": lib_name,
                                "dts_id": dts_id,
                                "dts_path": str(dts_abs_path),
                                "native_dir": str(type_dir),
                            },
                        )
                        self.publish_parse_event(event)
                        logger.info(f"Found native bridge: {lib_name} -> {dts_id}")

                        # Register consumer-side contract for cross-language matching
                        try:
                            registry = ContractRegistry()

                            # Infer expected artifact path (consumer expects the .so file)
                            # Typically in module/libs/{abi}/libname.so
                            # Use multiple ABI options for broader matching
                            for abi in ["arm64-v8a", "armeabi-v7a", "x86_64"]:
                                expected_artifact_path = config_file.parent / "libs" / abi / f"lib{lib_name}.so"
                                expected_artifact_id = self.graph_manager.normalize_path(
                                    expected_artifact_path
                                )

                                # Create consumer contract
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
                        except Exception as contract_err:
                            logger.warning(
                                "Failed to register contract for %s: %s",
                                lib_name,
                                contract_err,
                            )

            except (OSError, ValueError) as e:
                logger.warning(
                    "Could not process native dependency bridge for %s: %s",
                    lib_name,
                    e,
                )

    def _process_lock_file(self, result: Dict[str, Any], config_file_id: str):
        """Process oh-package-lock.json5 file."""
        for dep in result.get("external_dependencies", []):
            lib_name = dep.get("name")
            if not lib_name:
                continue

            lib_version = dep.get("version")
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
            )

            # Publish dependency event
            spec = DependencySpec(
                name=lib_name,
                version=str(lib_version) if lib_version else "",
                dependency_type=DependencyType.HVIGOR,
                parser_name=self.NAME,
                depth=0,
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

    def _process_hvigor_config(self, result: Dict[str, Any], config_file_id: str):
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
