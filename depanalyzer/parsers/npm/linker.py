"""NPM ecosystem linker.

This linker is responsible for configuration-driven relationships in
npm/Node.js projects, including:

- Module↔code membership based on directory containment
- Entry point linking (main, module, exports)
- Workspace package linking (monorepo support)
- Native addon bridging to CMake targets
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Set

from depanalyzer.graph import EdgeKind, GraphManager, NodeType
from depanalyzer.graph.models.linking import LinkClass
from depanalyzer.parsers.base import BaseLinker

logger = logging.getLogger("depanalyzer.parsers.npm.linker")


class NpmLinker(BaseLinker):
    """Linker for npm/Node.js ecosystem."""

    ECOSYSTEM = "npm"

    def link(self) -> None:
        """Apply npm linking logic to the current graph."""
        logger.info("NpmLinker: starting linking on current graph")

        cfg = getattr(self, "config", None)

        # Core linking operations
        self._attach_code_to_modules()
        self._link_entry_points()

        # Workspace linking (monorepo)
        enable_workspace_link = True
        if cfg is not None:
            enable_workspace_link = bool(
                getattr(cfg, "link_workspace_packages", True)
            )

        if enable_workspace_link:
            self._link_workspace_packages()
        else:
            logger.info("NpmLinker: workspace linking disabled by config")

        # Native addon bridging to CMake
        enable_native_link = True
        if cfg is not None:
            enable_native_link = bool(
                getattr(cfg, "enable_native_addon_link", True)
            )

        if enable_native_link:
            self._link_modules_to_cmake_targets()
        else:
            logger.info("NpmLinker: native addon linking disabled by config")

        logger.info("NpmLinker: linking completed")

    def _attach_code_to_modules(self) -> None:
        """Attach npm code files to owning modules by path containment."""
        graph_manager = self.graph_manager

        # Collect npm modules
        modules: Dict[str, Path] = {}
        for node_id, attrs in graph_manager.nodes():
            if attrs.get("type") != NodeType.MODULE.value:
                continue
            if attrs.get("parser_name") != "npm_parser":
                continue

            src_path = attrs.get("src_path")
            if not src_path:
                continue

            try:
                modules[node_id] = Path(src_path).resolve()
            except OSError:
                continue

        if not modules:
            logger.info("NpmLinker: no npm modules found for membership linking")
            return

        # Collect npm code nodes
        npm_code_nodes: List[tuple[str, Path]] = []
        for node_id, attrs in graph_manager.nodes():
            if attrs.get("type") != NodeType.CODE.value:
                continue

            parser_name = str(attrs.get("parser_name") or "")
            ecosystem = str(attrs.get("ecosystem") or "")

            if "npm" not in parser_name and ecosystem != "npm":
                continue

            src_path = attrs.get("src_path")
            if not src_path:
                continue

            try:
                npm_code_nodes.append((node_id, Path(src_path).resolve()))
            except OSError:
                continue

        if not npm_code_nodes:
            logger.info("NpmLinker: no npm code nodes found for membership linking")
            return

        # Create module→code edges based on path containment
        attached_edges = 0
        for code_id, code_path in npm_code_nodes:
            for module_id, module_root in modules.items():
                try:
                    code_path.relative_to(module_root)
                except ValueError:
                    continue

                graph_manager.add_edge(
                    module_id,
                    code_id,
                    edge_kind=EdgeKind.SOURCES.value,
                    parser_name="npm_linker",
                    link_class=LinkClass.BUILD_CONFIG.value,
                    derived_from="npm_module_membership",
                )
                attached_edges += 1

        logger.info(
            "NpmLinker: attached %d module->code edges (%d modules, %d code nodes)",
            attached_edges,
            len(modules),
            len(npm_code_nodes),
        )

    def _link_entry_points(self) -> None:
        """Link modules to their entry point files."""
        graph_manager = self.graph_manager

        linked_entries = 0

        for node_id, attrs in graph_manager.nodes():
            if attrs.get("type") != NodeType.MODULE.value:
                continue
            if attrs.get("parser_name") != "npm_parser":
                continue

            src_path = attrs.get("src_path")
            if not src_path:
                continue

            module_root = Path(src_path)

            # Check main entry
            main_entry = attrs.get("main")
            if main_entry:
                entry_path = module_root / main_entry
                if entry_path.exists():
                    self._link_entry_file(graph_manager, node_id, entry_path, "main")
                    linked_entries += 1

            # Check module entry (ES modules)
            module_entry = attrs.get("module_entry")
            if module_entry:
                entry_path = module_root / module_entry
                if entry_path.exists():
                    self._link_entry_file(graph_manager, node_id, entry_path, "module")
                    linked_entries += 1

        logger.info("NpmLinker: linked %d entry points", linked_entries)

    def _link_entry_file(
        self,
        graph_manager: GraphManager,
        module_id: str,
        entry_path: Path,
        entry_type: str,
    ) -> None:
        """Create edge from module to entry point file.

        Args:
            graph_manager: Graph manager instance.
            module_id: Module node ID.
            entry_path: Path to entry point file.
            entry_type: Type of entry (main, module, etc.).
        """
        # Find or create code node for entry file
        entry_id = None
        for node_id, attrs in graph_manager.nodes():
            if attrs.get("type") != NodeType.CODE.value:
                continue
            node_path = attrs.get("src_path")
            if node_path:
                try:
                    if Path(node_path).resolve() == entry_path.resolve():
                        entry_id = node_id
                        break
                except OSError:
                    continue

        if entry_id:
            graph_manager.add_edge(
                module_id,
                entry_id,
                edge_kind=EdgeKind.CONTAINS.value,
                parser_name="npm_linker",
                link_class=LinkClass.BUILD_CONFIG.value,
                entry_type=entry_type,
                derived_from="npm_entry_point",
            )

    def _link_workspace_packages(self) -> None:
        """Link workspace packages in monorepo setups."""
        graph_manager = self.graph_manager

        # Find root packages with workspaces
        workspace_roots: Dict[str, tuple[Path, List[str]]] = {}
        for node_id, attrs in graph_manager.nodes():
            if attrs.get("type") != NodeType.MODULE.value:
                continue
            if attrs.get("parser_name") != "npm_parser":
                continue

            workspace_patterns = attrs.get("workspace_patterns")
            if not workspace_patterns:
                continue

            src_path = attrs.get("src_path")
            if not src_path:
                continue

            try:
                workspace_roots[node_id] = (Path(src_path).resolve(), workspace_patterns)
            except OSError:
                continue

        if not workspace_roots:
            logger.info("NpmLinker: no workspace roots found")
            return

        # Find all npm modules
        all_modules: Dict[str, Path] = {}
        for node_id, attrs in graph_manager.nodes():
            if attrs.get("type") != NodeType.MODULE.value:
                continue
            if attrs.get("parser_name") != "npm_parser":
                continue

            src_path = attrs.get("src_path")
            if src_path:
                try:
                    all_modules[node_id] = Path(src_path).resolve()
                except OSError:
                    continue

        # Link workspace members to root
        linked_workspaces = 0
        for root_id, (root_path, patterns) in workspace_roots.items():
            for module_id, module_path in all_modules.items():
                if module_id == root_id:
                    continue

                # Check if module is under workspace root
                try:
                    rel_path = module_path.relative_to(root_path)
                except ValueError:
                    continue

                # Check if matches any workspace pattern
                if self._matches_workspace_pattern(str(rel_path), patterns):
                    graph_manager.add_edge(
                        root_id,
                        module_id,
                        edge_kind=EdgeKind.CONTAINS.value,
                        parser_name="npm_linker",
                        link_class=LinkClass.BUILD_CONFIG.value,
                        derived_from="npm_workspace",
                    )
                    linked_workspaces += 1

        logger.info("NpmLinker: linked %d workspace packages", linked_workspaces)

    def _matches_workspace_pattern(self, rel_path: str, patterns: List[str]) -> bool:
        """Check if path matches any workspace pattern.

        Args:
            rel_path: Relative path from workspace root.
            patterns: List of workspace patterns (glob-like).

        Returns:
            bool: True if path matches any pattern.
        """
        import fnmatch

        rel_path = rel_path.replace("\\", "/")

        for pattern in patterns:
            pattern = pattern.replace("\\", "/")

            # Handle patterns like "packages/*"
            if pattern.endswith("/*"):
                base = pattern[:-2]
                if rel_path.startswith(base + "/"):
                    # Check it's a direct child
                    remaining = rel_path[len(base) + 1 :]
                    if "/" not in remaining:
                        return True

            # Handle patterns like "packages/**"
            elif pattern.endswith("/**"):
                base = pattern[:-3]
                if rel_path.startswith(base + "/"):
                    return True

            # Direct match
            elif fnmatch.fnmatch(rel_path, pattern):
                return True

        return False

    def _link_modules_to_cmake_targets(self) -> None:
        """Link npm modules with native addons to CMake targets.

        This bridges npm packages that have native addons (binding.gyp,
        cmake-js) to their corresponding CMake build targets.
        """
        graph_manager = self.graph_manager

        # Find npm modules with native addons
        native_modules: Dict[str, List[Path]] = {}
        for node_id, attrs in graph_manager.nodes():
            if attrs.get("type") != NodeType.MODULE.value:
                continue
            if attrs.get("parser_name") != "npm_parser":
                continue
            if not attrs.get("has_native_addon"):
                continue

            native_dirs = attrs.get("native_dirs") or []
            dirs: List[Path] = []
            for nd in native_dirs:
                if isinstance(nd, str):
                    try:
                        dirs.append(Path(nd).resolve())
                    except OSError:
                        continue

            if dirs:
                native_modules[node_id] = dirs

        if not native_modules:
            logger.info("NpmLinker: no native addon modules found")
            return

        # Find CMake targets
        cmake_targets: Dict[str, str] = {}
        for node_id, attrs in graph_manager.nodes():
            node_type = attrs.get("type")
            parser_name = attrs.get("parser_name")

            if not parser_name or "cmake" not in str(parser_name).lower():
                continue

            if node_type not in [
                NodeType.SHARED_LIBRARY.value,
                NodeType.STATIC_LIBRARY.value,
                NodeType.MODULE.value,
                NodeType.TARGET.value,
            ]:
                continue

            src_path = attrs.get("src_path")
            if src_path:
                cmake_targets[node_id] = src_path

        if not cmake_targets:
            logger.info("NpmLinker: no CMake targets found for native addon bridging")
            return

        # Create associations based on path containment
        associations: List[tuple[str, str]] = []

        for module_id, native_dirs in native_modules.items():
            for target_id, src_path in cmake_targets.items():
                try:
                    target_path = Path(src_path).resolve()
                    target_dir = (
                        target_path.parent if target_path.is_file() else target_path
                    )
                except OSError:
                    continue

                for native_dir in native_dirs:
                    try:
                        # Check if CMake target is within native dir
                        target_dir.relative_to(native_dir)
                        associations.append((module_id, target_id))
                        break
                    except ValueError:
                        try:
                            # Or native dir is within CMake target dir
                            native_dir.relative_to(target_dir)
                            associations.append((module_id, target_id))
                            break
                        except ValueError:
                            continue

        if not associations:
            logger.info("NpmLinker: no module<->CMake associations found")
            return

        # Create edges
        created_edges = 0
        for module_id, target_id in associations:
            # Create intermediate native_build process node
            native_build_id = f"{module_id}:native_build"
            if not graph_manager.has_node(native_build_id):
                graph_manager.add_node(
                    native_build_id,
                    NodeType.PROCESS.value,
                    parser_name="npm_linker",
                    process_type="native_build",
                    provenance="npm_native_addon_bridge",
                )

            graph_manager.add_edge(
                module_id,
                native_build_id,
                edge_kind=EdgeKind.DEPENDS_ON.value,
                parser_name="npm_linker",
                link_class=LinkClass.BUILD_CONFIG.value,
                bridge="native_addon",
            )
            graph_manager.add_edge(
                native_build_id,
                target_id,
                edge_kind=EdgeKind.DEPENDS_ON.value,
                parser_name="npm_linker",
                link_class=LinkClass.BUILD_CONFIG.value,
                bridge="native_addon",
            )
            created_edges += 2

        logger.info(
            "NpmLinker: created %d native addon bridge edges (%d associations)",
            created_edges,
            len(associations),
        )
