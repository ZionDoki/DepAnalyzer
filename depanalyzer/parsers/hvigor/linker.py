"""Hvigor ecosystem linker.

This linker is responsible for configuration-driven relationships in
Hvigor/ArkTS projects, including:

- Module↔code membership based on directory containment.
- Hvigor module ↔ CMake target bridging via native directories (path抓手).
- Hvigor shared_library nodes wiring into C++ subgraphs using build
  contracts (artifact抓手).

The linker operates purely on the unified graph and ContractRegistry,
without relying on the event bus.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from depanalyzer.graph.contract_registry import ContractRegistry
from depanalyzer.graph import LinkClass
from depanalyzer.graph import EdgeKind, GraphManager, NodeType
from depanalyzer.parsers.base import BaseLinker

logger = logging.getLogger("depanalyzer.parsers.hvigor.linker")

# Hvigor module node types to consider for membership/packaging.
HVIGOR_MODULE_NODE_TYPES = {
    NodeType.MODULE.value,
    NodeType.HAP.value,
    NodeType.HAR.value,
    NodeType.HSP.value,
}


class HvigorLinker(BaseLinker):
    """Linker for Hvigor/ArkTS ecosystem."""

    ECOSYSTEM = "hvigor"

    def link(self) -> None:
        """Apply Hvigor linking logic to the current graph."""
        logger.info("HvigorLinker: starting linking on current graph")

        cfg = getattr(self, "config", None)

        # Module↔code membership and packaging are always useful for the
        # construct graph, so we keep them enabled unconditionally.
        self._attach_code_to_modules()
        self._link_packaging_targets()

        enable_path_bridge = True
        enable_shared_cpp_link = True
        if cfg is not None:
            enable_path_bridge = bool(getattr(cfg, "enable_path_bridge", True))
            enable_shared_cpp_link = bool(
                getattr(cfg, "enable_shared_library_cpp_link", True)
            )

        if enable_path_bridge:
            self._link_modules_to_cmake_targets_by_path()
        else:
            logger.info("HvigorLinker: path-based Hvigor↔CMake bridge disabled by config")

        if enable_shared_cpp_link:
            self._link_shared_libraries_to_cpp_roots()
        else:
            logger.info("HvigorLinker: shared-library→C++ linking disabled by config")

        logger.info("HvigorLinker: linking completed")

    # ------------------------------------------------------------------
    # Module↔code membership
    # ------------------------------------------------------------------

    def _attach_code_to_modules(self) -> None:
        """Attach Hvigor code files to owning modules by path containment."""
        graph_manager = self.graph_manager

        modules: Dict[str, Path] = {}
        for node_id, attrs in graph_manager.nodes():
            if attrs.get("type") not in HVIGOR_MODULE_NODE_TYPES:
                continue

            if attrs.get("parser_name") != "hvigor":
                continue

            src_path = attrs.get("src_path")
            if not src_path:
                continue

            try:
                modules[node_id] = Path(src_path).resolve()
            except OSError:
                continue

        if not modules:
            logger.info("HvigorLinker: no Hvigor modules found for membership linking")
            return

        hvigor_code_nodes: List[tuple[str, Path]] = []
        for node_id, attrs in graph_manager.nodes():
            if attrs.get("type") != NodeType.CODE.value:
                continue

            parser_name = str(attrs.get("parser_name") or "")
            if "hvigor" not in parser_name and "arkts" not in parser_name.lower():
                continue

            src_path = attrs.get("src_path")
            if not src_path:
                continue

            try:
                hvigor_code_nodes.append((node_id, Path(src_path).resolve()))
            except OSError:
                continue

        if not hvigor_code_nodes:
            logger.info("HvigorLinker: no Hvigor code nodes found for membership linking")
            return

        attached_edges = 0
        for code_id, code_path in hvigor_code_nodes:
            for module_id, module_root in modules.items():
                try:
                    code_path.relative_to(module_root)
                except ValueError:
                    continue

                graph_manager.add_edge(
                    module_id,
                    code_id,
                    edge_kind=EdgeKind.SOURCES.value,
                    parser_name="hvigor_linker",
                    link_class=LinkClass.BUILD_CONFIG.value,
                    derived_from="hvigor_module_membership",
                )
                attached_edges += 1

        logger.info(
            "HvigorLinker: attached %d module->code edges (%d modules, %d code nodes)",
            attached_edges,
            len(modules),
            len(hvigor_code_nodes),
        )

    # ------------------------------------------------------------------
    # Packaging (HAP/HAR/HSP) targets
    # ------------------------------------------------------------------

    def _link_packaging_targets(self) -> None:
        """Create packaging nodes (HAP/HAR/HSP) and link them to modules.

        Uses the `module_type` attribute recorded on module nodes by
        HvigorParser when parsing module.json/module.json5. For now we use a simple
        mapping:

        - module_type in {"entry", "feature"} -> HAP
        - module_type == "har" -> HAR
        - module_type == "hsp" -> HSP
        """
        graph_manager = self.graph_manager

        created_nodes = 0
        created_edges = 0

        for module_id, attrs in graph_manager.nodes():
            if attrs.get("type") not in HVIGOR_MODULE_NODE_TYPES:
                continue
            if attrs.get("parser_name") != "hvigor":
                continue

            module_type = attrs.get("module_type")
            node_type = attrs.get("type")
            if not module_type:
                if node_type == NodeType.HAP.value:
                    module_type = "hap"
                elif node_type == NodeType.HAR.value:
                    module_type = "har"
                elif node_type == NodeType.HSP.value:
                    module_type = "hsp"
                else:
                    continue

            module_name = attrs.get("name") or module_id.split(":", 1)[-1]

            package_type: Optional[str]
            if module_type in ("entry", "feature"):
                package_type = "hap"
            elif module_type == "har":
                package_type = "har"
            elif module_type == "hsp":
                package_type = "hsp"
            else:
                package_type = None

            if not package_type:
                continue

            package_id = f"{package_type}:{module_name}"
            if not graph_manager.has_node(package_id):
                graph_manager.add_node(
                    package_id,
                    package_type,
                    parser_name="hvigor_linker",
                    name=module_name,
                    origin="in_repo",
                    provenance="hvigor_packaging",
                )
                created_nodes += 1

            graph_manager.add_edge(
                package_id,
                module_id,
                edge_kind=EdgeKind.CONTAINS.value,
                parser_name="hvigor_linker",
                link_class=LinkClass.BUILD_CONFIG.value,
                derived_from="hvigor_packaging",
            )
            created_edges += 1

        logger.info(
            "HvigorLinker: created %d packaging nodes and %d package->module edges",
            created_nodes,
            created_edges,
        )

    # ------------------------------------------------------------------
    # Path-based Hvigor ↔ CMake bridging
    # ------------------------------------------------------------------

    def _link_modules_to_cmake_targets_by_path(self) -> None:
        """Link Hvigor modules to CMake targets using native_dir path containment."""
        graph_manager = self.graph_manager

        # Discover Hvigor modules and their native directories
        modules_native_dirs: Dict[str, List[Path]] = {}
        for node_id, attrs in graph_manager.nodes():
            if attrs.get("type") != NodeType.MODULE.value:
                continue
            if attrs.get("parser_name") != "hvigor":
                continue

            native_dirs = attrs.get("native_dirs") or []
            dirs: List[Path] = []
            for nd in native_dirs:
                if not isinstance(nd, str):
                    continue
                try:
                    dirs.append(Path(nd).resolve())
                except OSError:
                    continue

            if dirs:
                modules_native_dirs[node_id] = dirs

        if not modules_native_dirs:
            logger.info(
                "HvigorLinker: no native_dirs recorded on Hvigor modules; "
                "skipping path-based bridging"
            )
            return

        # Discover CMake-like targets
        candidate_targets: Dict[str, str] = {}
        for node_id, attrs in graph_manager.nodes():
            node_type = attrs.get("type")
            parser_name = attrs.get("parser_name")
            if not parser_name or "cmake" not in str(parser_name).lower():
                continue

            if node_type not in [
                NodeType.SHARED_LIBRARY.value,
                NodeType.STATIC_LIBRARY.value,
                NodeType.EXECUTABLE.value,
                NodeType.TARGET.value,
            ]:
                continue

            src_path = attrs.get("src_path")
            if not src_path:
                continue

            candidate_targets[node_id] = src_path

        if not candidate_targets:
            logger.info("HvigorLinker: no CMake-like targets found for path-based bridging")
            return

        associations: List[tuple[str, str]] = []  # (module_id, target_id)

        for module_id, native_dirs in modules_native_dirs.items():
            for target_id, src_path in candidate_targets.items():
                try:
                    target_path = Path(src_path).resolve()
                    target_dir = target_path.parent if target_path.is_file() else target_path
                except OSError:
                    continue

                for native_dir in native_dirs:
                    try:
                        # Associate when CMake target lives within the native_dir
                        # or when the native_dir is nested under the CMake target dir.
                        target_dir.relative_to(native_dir)
                        associate = True
                    except ValueError:
                        try:
                            native_dir.relative_to(target_dir)
                            associate = True
                        except ValueError:
                            associate = False

                    if not associate:
                        continue

                    associations.append((module_id, target_id))
                    break  # One native_dir is enough for this target

        if not associations:
            logger.info("HvigorLinker: no module<->CMake target associations found by path")
            return

        created_edges = 0
        for module_id, target_id in associations:
            native_build_id = f"{module_id}:native_build"
            if not graph_manager.has_node(native_build_id):
                graph_manager.add_node(
                    native_build_id,
                    NodeType.PROCESS,
                    parser_name="hvigor_linker",
                    process_type="native_build",
                    provenance="mixed_build_bridge",
                )

            graph_manager.add_edge(
                module_id,
                native_build_id,
                edge_kind=EdgeKind.DEPENDS_ON.value,
                parser_name="hvigor_linker",
                link_class=LinkClass.BUILD_CONFIG.value,
                bridge="path",
            )
            graph_manager.add_edge(
                native_build_id,
                target_id,
                edge_kind=EdgeKind.DEPENDS_ON.value,
                parser_name="hvigor_linker",
                link_class=LinkClass.BUILD_CONFIG.value,
                bridge="path",
            )
            created_edges += 2

        logger.info(
            "HvigorLinker: created %d path-based module<->CMake associations (%d pairs)",
            created_edges,
            len(associations),
        )

    # ------------------------------------------------------------------
    # Shared library ↔ C++ code roots via contracts
    # ------------------------------------------------------------------

    def _link_shared_libraries_to_cpp_roots(self) -> None:
        """Link Hvigor shared libraries to C++ subgraph roots using contracts."""
        graph_manager = self.graph_manager
        
        # Use the registry provided via BaseLinker/initialization
        # Fallback to new instance/legacy if not available (though strict DI is preferred)
        if hasattr(self, "contract_registry") and self.contract_registry:
            registry = self.contract_registry
        else:
            # Fallback to legacy singleton (or new instance if singleton removed)
            # Ideally this path should not be hit in the new architecture
            from depanalyzer.graph.contract_registry import ContractRegistry
            registry = ContractRegistry.get_instance()

        matched_contracts = registry.match_contracts()
        if not matched_contracts:
            logger.info("HvigorLinker: no complete contracts for shared-library linking")
            return

        applied_edges = 0
        processed_contracts = 0

        for contract in matched_contracts:
            metadata = contract.metadata or {}

            # Provider metadata from CMakeGraphBuilder includes target_id
            target_id = metadata.get("target_id")
            native_dir = metadata.get("native_dir")
            consumer_id = contract.consumer_artifact

            if not consumer_id or not target_id:
                continue

            # Ensure consumer node exists as a shared_library on Hvigor side
            self._ensure_hvigor_shared_library_node(
                graph_manager,
                consumer_id,
                contract.artifact_name,
            )

            # Collect C++ code nodes under the provider target
            cpp_nodes = _collect_cpp_code_under_target(graph_manager, target_id)
            if not cpp_nodes:
                continue

            root_nodes = _find_code_roots(graph_manager, cpp_nodes)
            if not root_nodes:
                continue

            # For license/static analysis we default to including isolated roots.
            roots_to_link = _filter_isolated_roots(
                graph_manager,
                root_nodes,
                cpp_nodes,
                include_isolated=True,
            )
            if not roots_to_link:
                continue

            evidence = list(contract.evidence)
            evidence.append("linker:HvigorSharedLibrary")
            if native_dir:
                evidence.append(f"native_dir:{native_dir}")

            for code_id in roots_to_link:
                graph_manager.add_edge(
                    consumer_id,
                    code_id,
                    edge_kind=EdgeKind.DEPENDS_ON.value,
                    parser_name="hvigor_linker",
                    confidence=contract.confidence,
                    evidence=evidence,
                    link_class=LinkClass.BUILD_CONFIG.value,
                    derived_from="hvigor_shared_library",
                )
                applied_edges += 1

            processed_contracts += 1

        logger.info(
            "HvigorLinker: processed %d contracts and created %d shared_library->code edges",
            processed_contracts,
            applied_edges,
        )

    @staticmethod
    def _ensure_hvigor_shared_library_node(
        graph_manager: GraphManager,
        node_id: str,
        artifact_name: str,
    ) -> None:
        """Ensure the given node is modeled as a shared_library on Hvigor side."""
        attrs = graph_manager.get_node(node_id)
        if attrs is None:
            graph_manager.add_node(
                node_id,
                NodeType.SHARED_LIBRARY,
                parser_name="hvigor_linker",
                artifact_name=artifact_name,
                origin="in_repo",
                provenance="hvigor_shared_library",
                linkage_kind="shared",
                ecosystem="hvigor",
            )
            return

        if attrs.get("type") != NodeType.SHARED_LIBRARY.value:
            graph_manager.update_node_attribute(
                node_id,
                "type",
                NodeType.SHARED_LIBRARY.value,
            )
            graph_manager.update_node_attribute(node_id, "linkage_kind", "shared")


def _collect_cpp_code_under_target(
    graph_manager: GraphManager,
    target_id: str,
) -> Set[str]:
    """Collect C++ code node IDs reachable from a CMake target."""
    if not graph_manager.has_node(target_id):
        logger.debug("HvigorLinker: target %s not present in graph", target_id)
        return set()

    visited: Set[str] = set()
    queue: List[str] = [target_id]
    cpp_nodes: Set[str] = set()

    while queue:
        current = queue.pop()
        if current in visited:
            continue
        visited.add(current)

        attrs = graph_manager.get_node(current) or {}
        node_type = attrs.get("type")
        parser_name = str(attrs.get("parser_name") or "")

        if node_type == NodeType.CODE.value and "cpp" in parser_name:
            cpp_nodes.add(current)

        for succ in graph_manager.successors(current):
            if succ not in visited:
                queue.append(succ)

    logger.debug(
        "HvigorLinker: collected %d C++ code nodes under target %s",
        len(cpp_nodes),
        target_id,
    )
    return cpp_nodes


def _find_code_roots(
    graph_manager: GraphManager,
    code_nodes: Iterable[str],
) -> Set[str]:
    """Find root code nodes in a subgraph (no code predecessors)."""
    code_set: Set[str] = set(code_nodes)
    root_nodes: Set[str] = set()

    for node_id in code_set:
        has_code_predecessor = False
        for pred in graph_manager.predecessors(node_id):
            if pred in code_set:
                has_code_predecessor = True
                break

        if not has_code_predecessor:
            root_nodes.add(node_id)

    logger.debug(
        "HvigorLinker: identified %d root code nodes in C++ subgraph", len(root_nodes)
    )
    return root_nodes


def _filter_isolated_roots(
    graph_manager: GraphManager,
    root_nodes: Iterable[str],
    code_nodes: Iterable[str],
    include_isolated: bool,
) -> Set[str]:
    """Optionally filter out purely isolated root nodes."""
    root_set: Set[str] = set(root_nodes)
    if include_isolated:
        return root_set

    code_set: Set[str] = set(code_nodes)
    result: Set[str] = set()

    for root in root_set:
        component: Set[str] = set()
        queue: List[str] = [root]

        while queue:
            current = queue.pop()
            if current in component:
                continue
            component.add(current)

            for neighbor in graph_manager.successors(current):
                if neighbor in code_set and neighbor not in component:
                    queue.append(neighbor)
            for neighbor in graph_manager.predecessors(current):
                if neighbor in code_set and neighbor not in component:
                    queue.append(neighbor)

        if len(component) > 1:
            result.add(root)

    logger.debug(
        "HvigorLinker: filtered code roots: %d kept, %d isolated dropped",
        len(result),
        len(root_set) - len(result),
    )
    return result
