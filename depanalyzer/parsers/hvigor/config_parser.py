"""HVigor configuration parser producing unified graph structures."""

# Parser must continue operating even when individual config files are malformed.


import logging
from pathlib import Path
from typing import Any, Dict, Iterable, Set

import json5

from depanalyzer.parsers.base import BaseParser, DependencySpec
from depanalyzer.graph import GraphManager, NodeType, EdgeKind
from depanalyzer.graph import NodeSpec, EdgeSpec
from depanalyzer.runtime.eventbus import Event, EventType
from depanalyzer.graph.contract import BuildInterfaceContract, ContractType
from depanalyzer.graph.contract_registry import ContractRegistry

logger = logging.getLogger("depanalyzer.parsers.hvigor.config")

# Hvigor module node types (including packaging-specific variants).
HVIGOR_MODULE_NODE_TYPES = {
    NodeType.MODULE.value,
    NodeType.HAP.value,
    NodeType.HAR.value,
    NodeType.HSP.value,
}


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
        contract_registry: Any | None = None,
    ) -> None:
        """Initialize Hvigor configuration parser.

        Args:
            workspace_root: Workspace root path.
            graph_manager: Transaction graph manager.
            eventbus: Event bus for publishing parse events.
            contract_registry: Contract registry.
            Returns:
            None.
        """
        super().__init__(workspace_root, graph_manager, eventbus, config=config, contract_registry=contract_registry)
        self._last_config_result: Dict[str, Any] = {}
        self._known_modules: set[str] = set()
        self._pending_root_packages: list[tuple[Dict[str, Any], str]] = []
        self._pending_root_locks: list[tuple[Dict[str, Any], str]] = []
        self._root_package_applied_modules: set[str] = set()
        self._root_lock_applied_modules: set[str] = set()
        self._native_module_hints: dict[str, dict[str, Any]] = {}

    def _resolve_module_node_type(self, module_type: str | None) -> NodeType:
        """Map hvigor module type strings to concrete NodeType variants."""
        if not module_type:
            return NodeType.MODULE

        module_type = str(module_type).lower()
        if module_type in {"entry", "feature", "hap"}:
            return NodeType.HAP
        if module_type == "har":
            return NodeType.HAR
        if module_type == "hsp":
            return NodeType.HSP
        return NodeType.MODULE

    def _should_update_module_node_type(
        self, existing_type: str | None, new_type: NodeType
    ) -> bool:
        """Return True when we should update a module node's type."""
        if not existing_type:
            return True
        if existing_type not in HVIGOR_MODULE_NODE_TYPES:
            return True
        if existing_type == NodeType.MODULE.value and new_type != NodeType.MODULE:
            return True
        return False

    @staticmethod
    def _is_hvigor_module(attrs: Dict[str, Any] | None) -> bool:
        """Check whether a node dict represents a Hvigor module."""
        if not attrs:
            return False
        return (
            attrs.get("parser_name") == HvigorParser.NAME
            and attrs.get("type") in HVIGOR_MODULE_NODE_TYPES
        )

    def _update_native_hint(
        self,
        module_name: str,
        *,
        native_dirs: Iterable[str] | None = None,
        native_artifacts: Iterable[str] | None = None,
        evidence: Iterable[str] | None = None,
        is_native: bool | None = None,
    ) -> None:
        """Accumulate native metadata for a module for later node updates."""
        hints = self._native_module_hints.setdefault(
            module_name,
            {
                "native_dirs": set(),
                "native_artifacts": set(),
                "evidence": set(),
                "is_native": False,
            },
        )

        if native_dirs:
            hints["native_dirs"].update(str(p) for p in native_dirs)
        if native_artifacts:
            hints["native_artifacts"].update(str(p) for p in native_artifacts)
        if evidence:
            hints["evidence"].update(str(e) for e in evidence)
        if is_native:
            hints["is_native"] = hints["is_native"] or bool(is_native)
        if hints["native_dirs"] or hints["native_artifacts"]:
            hints["is_native"] = True

    def _build_native_attrs(self, module_name: str) -> Dict[str, Any]:
        """Build native-related attribute payload for a module node."""
        hints = self._native_module_hints.get(module_name)
        if not hints:
            return {}

        attrs: Dict[str, Any] = {}
        native_dirs = sorted(hints.get("native_dirs") or [])
        native_artifacts = sorted(hints.get("native_artifacts") or [])
        evidence = sorted(hints.get("evidence") or [])

        if native_dirs:
            attrs["native_dirs"] = native_dirs
        if native_artifacts:
            attrs["native_artifacts"] = native_artifacts
        if evidence:
            attrs["native_evidence"] = evidence
        if hints.get("is_native") or native_dirs or native_artifacts:
            attrs["has_native_code"] = True

        return attrs

    def _merge_node_attributes(self, node_id: str, attrs: Dict[str, Any]) -> None:
        """Merge attributes into an existing node, preserving truthy flags."""
        if not self.graph_manager.has_node(node_id):
            return

        node = self.graph_manager.get_node(node_id) or {}

        for key, value in attrs.items():
            if value is None:
                continue

            if isinstance(value, list):
                existing = node.get(key) or []
                merged: list[Any] = list(existing) if isinstance(existing, list) else []
                for item in value:
                    if item not in merged:
                        merged.append(item)
                self.graph_manager.update_node_attribute(node_id, key, merged)
                node[key] = merged
            else:
                if key == "has_native_code" and node.get(key):
                    continue
                self.graph_manager.update_node_attribute(node_id, key, value)
                node[key] = value

    def _apply_native_attrs_to_node(self, module_id: str, module_name: str) -> None:
        """Apply accumulated native hints to an existing module node."""
        native_attrs = self._build_native_attrs(module_name)
        if not native_attrs:
            return
        self._merge_node_attributes(module_id, native_attrs)

    def _extract_native_dirs_from_external_native_options(
        self, module_name: str, module_root: Path, module_entry: Dict[str, Any]
    ) -> Set[str]:
        """Extract native directories from build-profile externalNativeOptions."""
        opts = module_entry.get("externalNativeOptions")
        if not opts:
            return set()

        native_paths: Set[str] = set()

        def _collect_paths(candidate: Any) -> list[str]:
            if isinstance(candidate, str) and candidate.strip():
                return [candidate]
            if isinstance(candidate, (list, tuple)):
                return [str(item) for item in candidate if isinstance(item, str) and item.strip()]
            return []

        if isinstance(opts, dict):
            for key in ("path", "paths", "cmakeListsPath", "buildFile"):
                native_paths.update(_collect_paths(opts.get(key)))
            # Some configs may specify the path directly without a known key.
            for value in opts.values():
                native_paths.update(_collect_paths(value))
        else:
            native_paths.update(_collect_paths(opts))

        resolved_dirs: Set[str] = set()
        for raw_path in native_paths:
            try:
                path_obj = Path(raw_path)
                if not path_obj.is_absolute():
                    path_obj = (module_root / path_obj).resolve()
                else:
                    path_obj = path_obj.resolve()
                resolved_dirs.add(str(path_obj if path_obj.is_dir() else path_obj.parent))
            except OSError:
                continue

        evidence = ["externalNativeOptions"]
        if resolved_dirs:
            self._update_native_hint(
                module_name,
                native_dirs=resolved_dirs,
                evidence=evidence,
                is_native=True,
            )
        else:
            self._update_native_hint(
                module_name,
                evidence=evidence,
                is_native=True,
            )
        return resolved_dirs

    def _scan_module_native_artifacts(self, module_name: str, module_root: Path) -> None:
        """Scan common Hvigor native output locations for shared libraries."""
        native_dirs: Set[str] = set()
        native_artifacts: Set[str] = set()

        candidates = [
            module_root / "libs",
            module_root / "src" / "main" / "libs",
        ]

        for root_dir in candidates:
            if not root_dir.is_dir():
                continue
            try:
                for so_path in root_dir.rglob("*.so"):
                    if "node_modules" in so_path.parts:
                        continue
                    try:
                        normalized = (
                            self.graph_manager.normalize_path(so_path)
                            if self.graph_manager.root_path
                            else str(so_path.resolve())
                        )
                    except Exception:
                        normalized = str(so_path.resolve())
                    native_artifacts.add(normalized)
                    try:
                        native_dirs.add(str(so_path.parent.resolve()))
                    except OSError:
                        native_dirs.add(str(so_path.parent))
            except (OSError, ValueError) as exc:
                logger.debug("Skipping native scan for %s: %s", module_root, exc)

        if native_dirs or native_artifacts:
            self._update_native_hint(
                module_name,
                native_dirs=native_dirs,
                native_artifacts=native_artifacts,
                evidence=["shared_library_present"],
                is_native=True,
            )

    def _collect_native_metadata(
        self, module_name: str, module_root: Path, module_entry: Dict[str, Any] | None
    ) -> None:
        """Collect native evidence from config entry and on-disk artifacts."""
        if module_entry:
            self._extract_native_dirs_from_external_native_options(
                module_name, module_root, module_entry
            )
        self._scan_module_native_artifacts(module_name, module_root)

    def _ensure_module_node(
        self,
        module_name: str,
        *,
        provenance: str = "package_config",
        declared_via: str = "oh-package.json5",
        module_root_override: Path | None = None,
        over_approx: bool = False,
        confidence: float = 0.9,
        module_type: str | None = None,
    ) -> str:
        """Ensure a module node exists for the given module name."""
        module_id = f"module:{module_name}"
        module_root = module_root_override or (self.workspace_root / module_name).resolve()
        resolved_type = self._resolve_module_node_type(module_type)

        self._collect_native_metadata(module_name, module_root, None)
        native_attrs = self._build_native_attrs(module_name)

        attrs = {
            "origin": "in_repo",
            "provenance": provenance,
            "declared_via": declared_via,
        }
        attrs.update(native_attrs)

        existing = self.graph_manager.get_node(module_id)
        if existing is None:
            module_spec = NodeSpec(
                id=module_id,
                type=resolved_type,
                label=module_id,
                src_path=str(module_root),
                name=module_name,
                parser_name=self.NAME,
                confidence=confidence,
                over_approx=over_approx,
                attrs=attrs,
            )
            self.graph_manager.add_node_spec(module_spec)
        else:
            if self._should_update_module_node_type(existing.get("type"), resolved_type):
                self.graph_manager.update_node_attribute(module_id, "type", resolved_type.value)
            self._merge_node_attributes(module_id, attrs)
            # Ensure src_path is populated for path-based reasoning.
            if not existing.get("src_path"):
                self.graph_manager.update_node_attribute(module_id, "src_path", str(module_root))
        return module_id

    def parse(self, target_path: Path) -> None:
        """Parse a single config file and update the graph.

        Args:
            target_path: Config file path.
        """
        parsed_data = self._parse_single_config(target_path)
        self._last_config_result = parsed_data
        self._process_config_result(parsed_data)

    def _parse_single_config(self, file_path: Path) -> Dict[str, Any]:
        """Parse a single JSON/JSON5 config file and classify its content."""
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
                result["package_name"] = config.get("name")
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

            elif file_path.name in {"module.json5", "module.json"}:
                result["type"] = "module_config"
                module_cfg = config.get("module", {}) or {}
                result["module_name"] = module_cfg.get("name", file_path.parent.name)
                result["module_type"] = module_cfg.get("type")
                result["declared_via"] = file_path.name
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
            module_type = module_item.get("type") if isinstance(module_item, dict) else None
            if not module_name or not isinstance(module_name, str):
                continue

            module_id = f"module:{module_name}"
            module_root = Path(self.workspace_root) / module_name
            self._known_modules.add(module_name)
            resolved_type = self._resolve_module_node_type(module_type)

            module_entry = module_item if isinstance(module_item, dict) else None
            self._collect_native_metadata(module_name, module_root.resolve(), module_entry)
            native_attrs = self._build_native_attrs(module_name)

            attrs = {
                "origin": "in_repo",
                "provenance": "build_profile",
                "declared_via": "build-profile.json5",
                "module_type": module_type,
            }
            attrs.update(native_attrs)

            existing = self.graph_manager.get_node(module_id)
            if existing is None:
                module_spec = NodeSpec(
                    id=module_id,
                    type=resolved_type,
                    label=module_id,
                    src_path=str(module_root.resolve()),
                    name=module_name,
                    parser_name=self.NAME,
                    confidence=1.0,
                    attrs=attrs,
                )
                self.graph_manager.add_node_spec(module_spec)
            else:
                if self._should_update_module_node_type(existing.get("type"), resolved_type):
                    self.graph_manager.update_node_attribute(
                        module_id, "type", resolved_type.value
                    )
                if not existing.get("src_path"):
                    self.graph_manager.update_node_attribute(
                        module_id, "src_path", str(module_root.resolve())
                    )
                self._merge_node_attributes(module_id, attrs)

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

        self._flush_pending_root_packages()
        self._flush_pending_root_locks()

    def _process_module_config(self, result: Dict[str, Any], config_file_id: str):
        """Process module.json/module.json5 file."""
        module_name = result.get("module_name")
        if not module_name:
            return

        config_file = result.get("file")
        if not config_file:
            return
        module_id = f"module:{module_name}"
        module_root = config_file.parent.parent.parent

        module_type = result.get("module_type")
        declared_via = result.get("declared_via") or config_file.name
        resolved_type = self._resolve_module_node_type(module_type)

        self._collect_native_metadata(module_name, module_root.resolve(), None)
        native_attrs = self._build_native_attrs(module_name)

        attrs = {
            "origin": "in_repo",
            "provenance": "module_config",
            "declared_via": declared_via,
            "module_type": module_type,
        }
        attrs.update(native_attrs)

        existing = self.graph_manager.get_node(module_id)
        if existing is None:
            module_spec = NodeSpec(
                id=module_id,
                type=resolved_type,
                label=module_id,
                src_path=str(module_root.resolve()),
                name=module_name,
                parser_name=self.NAME,
                confidence=1.0,
                attrs=attrs,
            )
            self.graph_manager.add_node_spec(module_spec)
        else:
            if self._should_update_module_node_type(existing.get("type"), resolved_type):
                self.graph_manager.update_node_attribute(
                    module_id, "type", resolved_type.value
                )
            if module_type and existing.get("module_type") != module_type:
                self.graph_manager.update_node_attribute(module_id, "module_type", module_type)
            if not existing.get("src_path"):
                self.graph_manager.update_node_attribute(
                    module_id, "src_path", str(module_root.resolve())
                )
            self._merge_node_attributes(module_id, attrs)

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
                "declared_via": declared_via,
            },
        )
        self.publish_parse_event(event)

        self._flush_pending_root_packages()
        self._flush_pending_root_locks()

    def _get_workspace_modules(self) -> Dict[str, str]:
        """Get mapping of package names to module names for workspace modules."""
        workspace_map: Dict[str, str] = {}
        build_profile = self.workspace_root / "build-profile.json5"
        if not build_profile.exists():
            return {name: name for name in self._known_modules}

        try:
            with open(build_profile, "r", encoding="utf-8") as f:
                data = json5.load(f)

            for mod in data.get("modules", []):
                mod_name = mod.get("name") if isinstance(mod, dict) else mod
                if not mod_name or not isinstance(mod_name, str):
                    continue

                # mod_name is usually the directory name relative to root
                # Add the directory name itself as a known local name
                workspace_map[mod_name] = mod_name
                self._known_modules.add(mod_name)

                # Check for package name alias in oh-package.json5
                mod_path = self.workspace_root / mod_name
                pkg_file = mod_path / "oh-package.json5"

                if pkg_file.exists():
                    try:
                        with open(pkg_file, "r", encoding="utf-8") as pf:
                            pkg_data = json5.load(pf)
                            pkg_name = pkg_data.get("name")
                            if pkg_name:
                                workspace_map[pkg_name] = mod_name
                    except Exception:
                        pass

        except Exception as e:
            logger.warning(
                "Failed to scan workspace modules: %s", e
            )

        if not workspace_map and self._known_modules:
            return {name: name for name in self._known_modules}

        return workspace_map

    def _collect_module_ids(self) -> list[str]:
        """Return current Hvigor module node ids present in the graph.

        Restrict to modules created by this parser to avoid mixing in
        other ecosystems' module nodes.
        """
        module_ids: list[str] = []
        for node_id, attrs in self.graph_manager.nodes():
            if not self._is_hvigor_module(attrs):
                continue
            module_ids.append(str(node_id))
        return module_ids

    def _should_infer_missing_modules(self) -> bool:
        """Return True when the parser should synthesize modules from configs."""
        cfg = getattr(self, "config", None)
        if cfg is None:
            return True
        return bool(getattr(cfg, "infer_missing_modules", True))

    def _infer_root_module(
        self, result: Dict[str, Any], declared_via: str
    ) -> str | None:
        """Create a best-effort root module when no modules are known."""
        module_name = result.get("package_name") or self.workspace_root.name or "workspace_root"
        try:
            module_id = self._ensure_module_node(
                module_name,
                provenance="package_config",
                declared_via=declared_via,
                module_root_override=self.workspace_root,
                over_approx=True,
                confidence=0.7,
            )
            self._known_modules.add(module_name)
            return module_id
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to infer root module from %s: %s", declared_via, exc)
            return None

    def _flush_pending_root_packages(self) -> None:
        """Re-process deferred root-level oh-package.json5 files once modules exist."""
        module_ids = self._collect_module_ids()
        if not module_ids or not self._pending_root_packages:
            return

        new_targets = [mid for mid in module_ids if mid not in self._root_package_applied_modules]
        if not new_targets:
            return

        pending = list(self._pending_root_packages)
        for result, config_file_id in pending:
            self._process_package_dependencies(
                result,
                config_file_id,
                target_module_ids_override=new_targets,
            )
        self._root_package_applied_modules.update(new_targets)

    def _flush_pending_root_locks(self) -> None:
        """Re-process deferred root-level oh-package-lock.json5 files once modules exist."""
        module_ids = self._collect_module_ids()
        if not module_ids or not self._pending_root_locks:
            return

        new_targets = [mid for mid in module_ids if mid not in self._root_lock_applied_modules]
        if not new_targets:
            return

        pending = list(self._pending_root_locks)
        for result, config_file_id in pending:
            self._process_lock_file(
                result,
                config_file_id,
                target_module_ids_override=new_targets,
            )
        self._root_lock_applied_modules.update(new_targets)

    def _process_package_dependencies(
        self,
        result: Dict[str, Any],
        config_file_id: str,
        target_module_ids_override: list[str] | None = None,
    ):
        """Process oh-package.json5 dependencies."""
        config_file = result.get("file")
        workspace_map = self._get_workspace_modules()
        target_module_ids = []

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
                owning_module_id = self._ensure_module_node(module_name)

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

        if target_module_ids_override is not None:
            target_module_ids = list(target_module_ids_override)
        elif owning_module_id:
            target_module_ids = [owning_module_id]
        else:
            # Root-level deps apply to all discovered modules in the workspace.
            module_names = {name for name in workspace_map.values()}
            # Fall back to existing module nodes when build-profile is missing.
            if not module_names:
                module_names = {
                    n.split("module:", 1)[1]
                    for n, attrs in self.graph_manager.nodes()
                    if isinstance(n, str)
                    and n.startswith("module:")
                    and self._is_hvigor_module(attrs)
                }
            if not module_names and self._known_modules:
                module_names = set(self._known_modules)
            created_fallback = False
            if not module_names and self._should_infer_missing_modules():
                inferred = self._infer_root_module(result, "oh-package.json5")
                if inferred:
                    target_module_ids = [inferred]
                    created_fallback = True
                    edge_spec = EdgeSpec(
                        source=inferred,
                        target=config_file_id,
                        kind=EdgeKind.DEFINED_BY,
                        parser_name=self.NAME,
                    )
                    self.graph_manager.add_edge_spec(edge_spec)
            if not module_names:
                # Defer to pending queue so newly discovered modules also get the deps.
                self._pending_root_packages.append((result, config_file_id))
                if owning_module_id is None and target_module_ids:
                    self._root_package_applied_modules.update(target_module_ids)
                if not target_module_ids:
                    return
            if not target_module_ids:
                target_module_ids = [self._ensure_module_node(name) for name in module_names]
            if owning_module_id is None:
                self._root_package_applied_modules.update(target_module_ids)

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

            # Check if this is a local (file:) dependency or internal module
            is_explicit_local = isinstance(dep_version, str) and dep_version.startswith(
                "file:"
            )
            local_module_name = workspace_map.get(dep_name)

            if is_explicit_local or local_module_name:
                # Local dependency - reference to another module or local package
                if local_module_name:
                    target_module_name = local_module_name
                else:
                    # Fallback for explicit file: deps not in map
                    target_module_name = (
                        dep_name.split("/")[-1] if "/" in dep_name else dep_name
                    )

                target_module_id = f"module:{target_module_name}"

                # Ensure target module node exists so it does not stay as type=unknown
                target_existing = self.graph_manager.get_node(target_module_id)
                resolved_target_type = self._resolve_module_node_type(
                    (target_existing or {}).get("module_type") if target_existing else None
                )
                if target_existing is None:
                    target_root = (self.workspace_root / target_module_name).resolve()
                    target_spec = NodeSpec(
                        id=target_module_id,
                        type=resolved_target_type,
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
                elif self._should_update_module_node_type(
                    target_existing.get("type"), resolved_target_type
                ):
                    self.graph_manager.update_node_attribute(
                        target_module_id, "type", resolved_target_type.value
                    )

                # Create edge from owning module to target module
                for module_id in target_module_ids:
                    edge_spec = EdgeSpec(
                        source=module_id,
                        target=target_module_id,
                        kind=EdgeKind.DEPENDS_ON,
                        parser_name=self.NAME,
                    )
                    self.graph_manager.add_edge_spec(edge_spec)
                    logger.debug(
                        "Created local dependency edge: %s -> %s",
                        module_id,
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
                for module_id in target_module_ids:
                    edge_spec = EdgeSpec(
                        source=module_id,
                        target=lib_id,
                        kind=EdgeKind.DEPENDS_ON,
                        parser_name=self.NAME,
                    )
                    self.graph_manager.add_edge_spec(edge_spec)
                    logger.debug(
                        "Created external dependency edge: %s -> %s",
                        module_id,
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
                    # Provide rich metadata so the dep fetcher can handle
                    # monorepo/local scenarios without unnecessary cloning.
                    path_hint = dep_name.split("/")[-1] if dep_name else None
                    spec = DependencySpec(
                        name=dep_name,
                        version=str(dep_version) if dep_version else "",
                        ecosystem="hvigor",
                        metadata={
                            "parser_name": self.NAME,
                            "depth": 0,
                            "workspace_root": str(self.workspace_root),
                            "owner_module": owning_module_id,
                            "declared_via": "oh-package.json5",
                            "registry_type": "ohpm",
                            "path_hint": path_hint,
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
                    registry = self.contract_registry

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
                                type=NodeType.SHARED_LIBRARY,
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
                                    "linkage_kind": "shared",
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

    def _process_lock_file(
        self,
        result: Dict[str, Any],
        _config_file_id: str,
        target_module_ids_override: list[str] | None = None,
    ):
        """Process oh-package-lock.json5 file."""
        config_file = result.get("file")
        workspace_map = self._get_workspace_modules()
        target_module_ids = []

        # Determine owning module from lock file path (same logic as oh-package.json5)
        owning_module_id = None
        try:
            rel_to_root = config_file.relative_to(self.workspace_root)
            parts = rel_to_root.parts
            if len(parts) >= 2:
                module_name = parts[0]
                owning_module_id = self._ensure_module_node(module_name)
        except ValueError:
            pass

        if target_module_ids_override is not None:
            target_module_ids = list(target_module_ids_override)
        elif owning_module_id:
            target_module_ids = [owning_module_id]
        else:
            module_names = {name for name in workspace_map.values()}
            if not module_names:
                module_names = {
                    n.split("module:", 1)[1]
                    for n, attrs in self.graph_manager.nodes()
                    if isinstance(n, str)
                    and n.startswith("module:")
                    and self._is_hvigor_module(attrs)
                }
            if not module_names and self._known_modules:
                module_names = set(self._known_modules)
            created_fallback = False
            if not module_names and self._should_infer_missing_modules():
                inferred = self._infer_root_module(result, "oh-package-lock.json5")
                if inferred:
                    target_module_ids = [inferred]
                    created_fallback = True
            if not module_names:
                self._pending_root_locks.append((result, _config_file_id))
                if owning_module_id is None and target_module_ids:
                    self._root_lock_applied_modules.update(target_module_ids)
                if not target_module_ids:
                    return
            if not target_module_ids:
                target_module_ids = [self._ensure_module_node(name) for name in module_names]
            if owning_module_id is None:
                self._root_lock_applied_modules.update(target_module_ids)

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
            for module_id in target_module_ids:
                self.add_edge(
                    source=module_id,
                    target=lib_id,
                    edge_kind=EdgeKind.DEPENDS_ON,
                    parser_name=self.NAME,
                )
                logger.debug(
                    "Created lock file dependency edge: %s -> %s",
                    module_id,
                    lib_id,
                )

            # Publish dependency event
            path_hint = lib_name.split("/")[-1] if lib_name else None
            spec = DependencySpec(
                name=lib_name,
                version=str(lib_version) if lib_version else "",
                ecosystem="hvigor",
                metadata={
                    "parser_name": self.NAME,
                    "depth": 0,
                    "registry_type": registry_type,
                    "workspace_root": str(self.workspace_root),
                    "owner_module": owning_module_id,
                    "declared_via": "oh-package-lock.json5",
                    "path_hint": path_hint,
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
