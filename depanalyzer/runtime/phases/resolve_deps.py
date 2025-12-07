"""
RESOLVE_DEPS phase implementation.

This phase discovers dependencies, fetches them, and spawns child
transactions in parallel to analyze third-party dependencies.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List

from networkx.exception import NetworkXNoCycle

from depanalyzer.graph import GlobalDAG, NodeType
from depanalyzer.parsers.base import DependencySpec
from depanalyzer.runtime.dependency_resolver import resolve_dependencies
from depanalyzer.runtime.phases.base import BasePhase
from depanalyzer.runtime.context import TransactionContext

logger = logging.getLogger("depanalyzer.transaction.phase.resolve_deps")

_SAFE_EXCEPTIONS = (
    RuntimeError,
    ValueError,
    TypeError,
    AttributeError,
    KeyError,
    IndexError,
    OSError,
    ImportError,
    LookupError,
)


class ResolveDepsPhase(BasePhase):
    """
    RESOLVE_DEPS phase: Resolve third-party dependencies.

    This phase:
    1. Discovers dependencies from DependencyCollector
    2. Fetches dependencies to local cache
    3. Creates child transactions to analyze each dependency
    4. Links child graphs into parent graph
    5. Updates GlobalDAG with dependency relationships

    Critical: False - Dependency resolution failures shouldn't halt analysis.
    """

    IS_CRITICAL = False  # Non-critical: partial dependency resolution is acceptable

    def __init__(self, state: "TransactionState") -> None:
        super().__init__(state)
        # Mapping from DependencySpec to list of originating graph node IDs
        self._dependency_node_map: Dict[DependencySpec, List[str]] = {}

    def execute(self, context: TransactionContext) -> None:
        """Execute dependency resolution logic."""
        # Check if dependency resolution is enabled
        if not self.state.enable_dependency_resolution:
            logger.info("Dependency resolution disabled, skipping phase")
            return

        # Check if we've reached max depth
        if self.state.max_dependency_depth <= 0:
            logger.info("Max dependency depth reached, skipping resolution")
            return

        # Discover dependencies
        dep_specs = self._discover_dependencies()

        if not dep_specs:
            logger.info("No dependencies found")
            self._clear_global_dag_dependencies()
            return

        logger.info("Found %d dependencies to resolve", len(dep_specs))

        # Apply max dependency limit
        if self.state.max_dependencies and len(dep_specs) > self.state.max_dependencies:
            logger.info(
                "Applying max limit: %d of %d",
                self.state.max_dependencies,
                len(dep_specs),
            )
            dep_specs = dep_specs[: self.state.max_dependencies]

        # Resolve dependencies (fetch to local cache)
        resolved_deps = resolve_dependencies(
            dep_specs,
            cache_root=self.state.dep_cache_root or Path(".depanalyzer_cache/deps"),
        )

        # Filter successful and non-self dependencies
        successful_deps = self._filter_successful_deps(resolved_deps)

        if not successful_deps:
            logger.warning("No dependencies successfully fetched")
            return

        logger.info(
            "Successfully fetched %d/%d dependencies",
            len(successful_deps),
            len(resolved_deps),
        )

        # Create and run child transactions
        child_graph_ids = self._process_child_transactions(successful_deps)

        # Update GlobalDAG
        self._update_global_dag(child_graph_ids)

    def _discover_dependencies(self) -> List[DependencySpec]:
        """Discover dependencies from DependencyCollector and graph."""
        # Use a dict keyed by spec to handle deduplication while aggregating nodes
        specs_map: Dict[DependencySpec, List[str]] = {}

        # Method 1: Get from DependencyCollector (event-driven)
        if self.state.dependency_collector:
            discovered = self.state.dependency_collector.get_discovered_dependencies()
            for spec in discovered:
                # Specs from collector might not have associated graph nodes
                if spec not in specs_map:
                    specs_map[spec] = []
            logger.debug("Discovered %d dependencies from collector", len(discovered))

        # Method 2: Scan GraphManager for external dependency nodes
        if self.state.graph_manager:
            for node_id, node_attrs in self.state.graph_manager.nodes():
                raw_type = node_attrs.get("type") or node_attrs.get("node_type")
                if isinstance(raw_type, NodeType):
                    normalized_type = raw_type.value
                else:
                    normalized_type = raw_type

                if normalized_type in (
                    NodeType.EXTERNAL_DEP.value,
                    NodeType.EXTERNAL_LIBRARY.value,
                ):
                    # Skip built-in modules (Node.js fs, path, etc.)
                    if node_attrs.get("is_builtin"):
                        continue
                    ecosystem = node_attrs.get("ecosystem")
                    name = node_attrs.get("name")
                    version = node_attrs.get("version")
                    if ecosystem and name:
                        spec = DependencySpec(
                            ecosystem=ecosystem,
                            name=name,
                            version=version or "*",
                        )
                        # Initialize list if new spec, then append node_id
                        if spec not in specs_map:
                            specs_map[spec] = []
                        specs_map[spec].append(node_id)

        # Store the mapping for later linking
        self._dependency_node_map = specs_map
        return list(specs_map.keys())

    def _filter_successful_deps(
        self, resolved_deps: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Filter successful deps and remove self-dependencies."""
        successful = [dep for dep in resolved_deps if dep.get("success")]

        # Filter out self-dependencies
        try:
            workspace_root = Path(self.state.workspace.root_path).resolve()
        except _SAFE_EXCEPTIONS:
            return successful

        filtered = []
        seen_paths = set()
        for dep in successful:
            source = dep.get("source")
            if not source:
                filtered.append(dep)
                continue

            try:
                dep_root = Path(source).resolve()
                if dep_root == workspace_root:
                    logger.info("Skipping self-dependency %s", dep.get("name"))
                    continue
                if dep_root in seen_paths:
                    logger.info(
                        "Skipping duplicate dependency path already resolved: %s",
                        dep_root,
                    )
                    continue
                seen_paths.add(dep_root)
            except _SAFE_EXCEPTIONS:
                pass

            filtered.append(dep)

        return filtered

    def _process_child_transactions(
        self, successful_deps: List[Dict[str, Any]]
    ) -> List[str]:
        """Create child transactions and collect their graph IDs.

        Child transactions are executed inline (sequentially) to avoid nested
        process pools which cause deadlocks on Windows. The GlobalTaskPool
        architecture ensures all parallelism happens at the task level, not
        at the transaction level.
        """
        factory = self.state.child_transaction_factory

        if not factory:
            logger.error(
                "No TransactionFactory available, cannot create child transactions"
            )
            return []

        child_graph_ids: List[str] = []
        completed, failed = 0, 0

        for dep in successful_deps:
            logger.info("Creating child transaction for: %s", dep["name"])

            # Persist dependency metadata on originating nodes before spawning child runs.
            self._attach_dependency_metadata(dep)

            graph_metadata: Dict[str, Any] = {}
            dep_metadata = dep.get("metadata") or {}
            if dep_metadata:
                graph_metadata["dependency_metadata"] = dep_metadata
            if dep.get("name"):
                graph_metadata["name"] = dep.get("name")
            if dep.get("version"):
                graph_metadata["version"] = dep.get("version")
            for meta_key in ("resolved_version", "license", "license_only"):
                if dep.get(meta_key) is not None:
                    graph_metadata[meta_key] = dep.get(meta_key)

            # Use factory to create child transaction (avoids circular import)
            child_tx = factory.create(
                source=dep["source"],
                graph_id=None,
                max_workers=self.state.max_workers,
                max_dependency_depth=self.state.max_dependency_depth - 1,
                parent_transaction_id=self.state.transaction_id,
                enable_dependency_resolution=self.state.enable_dependency_resolution,
                max_dependencies=self.state.max_dependencies,
                graph_cache_root=self.state.graph_cache_root,
                dep_cache_root=self.state.dep_cache_root,
                workspace_cache_root=self.state.workspace_cache_root,
                graph_build_config=self.state.graph_build_config,
                graph_metadata=graph_metadata,
            )

            # Execute child transaction inline to avoid nested process pools
            try:
                result = child_tx.run()
                if result.success:
                    completed += 1
                    logger.info(
                        "Child %s completed: %s (%d nodes, %d edges)",
                        child_tx.transaction_id,
                        dep["name"],
                        result.node_count,
                        result.edge_count,
                    )
                    self._link_dependency_graph(result.graph_id, dep)
                    if result.graph_id:
                        child_graph_ids.append(result.graph_id)
                else:
                    failed += 1
                    logger.error(
                        "Child %s failed: %s",
                        child_tx.transaction_id,
                        result.error,
                    )
            except Exception as exc:  # pragma: no cover - defensive
                failed += 1
                logger.error(
                    "Child %s failed with exception: %s",
                    child_tx.transaction_id,
                    exc,
                )

        logger.info(
            "Resolution completed: %d succeeded, %d failed",
            completed,
            failed,
        )
        return child_graph_ids

    def _attach_dependency_metadata(self, dep_info: Dict[str, Any]) -> None:
        """Embed dependency metadata into originating nodes for export.

        Args:
            dep_info: Dependency resolution record containing metadata.

        Returns:
            None
        """
        graph = self.state.graph_manager
        if graph is None:
            return

        spec = dep_info.get("spec")
        metadata = dep_info.get("metadata")
        if not spec or not metadata:
            return

        node_ids = self._dependency_node_map.get(spec, [])
        if not node_ids:
            return

        for node_id in node_ids:
            try:
                if metadata.get("license") is not None:
                    graph.update_node_attribute(node_id, "license", metadata.get("license"))
                if "license_only" in metadata:
                    graph.update_node_attribute(
                        node_id, "license_only", metadata.get("license_only")
                    )
                if metadata.get("resolved_version"):
                    graph.update_node_attribute(
                        node_id, "resolved_version", metadata.get("resolved_version")
                    )
                graph.update_node_attribute(node_id, "dependency_metadata", metadata)
            except ValueError:
                logger.debug("Node %s disappeared before metadata attachment", node_id)

    def _link_dependency_graph(
        self, dep_graph_id: str, dep_info: Dict[str, Any]
    ) -> None:
        """Link dependency graph into parent graph (creates proxy nodes)."""
        if not self.state.graph_manager or not dep_graph_id:
            return

        spec = dep_info.get("spec")
        if not spec:
            logger.warning("No dependency spec found for %s", dep_graph_id)
            return

        node_ids = self._dependency_node_map.get(spec, [])
        if not node_ids:
            logger.debug("No originating nodes found for dependency %s", spec)
            return

        logger.info(
            "Linking dependency graph %s to %d parent nodes",
            dep_graph_id,
            len(node_ids),
        )

        for node_id in node_ids:
            try:
                attrs = self.state.graph_manager.get_node(node_id) or {}
                prior_type = attrs.get("type")
                src_hint = dep_info.get("source")
                try:
                    src_path = str(Path(src_hint).resolve()) if src_hint else None
                except OSError:
                    src_path = src_hint

                # Update node to point to the resolved dependency graph and
                # upgrade to a package-style node so license/metadata flow
                # through a stable container.
                self.state.graph_manager.update_node_attribute(
                    node_id, "child_graph_id", dep_graph_id
                )
                self.state.graph_manager.update_node_attribute(
                    node_id, "type", NodeType.MODULE.value
                )
                if src_path:
                    self.state.graph_manager.update_node_attribute(
                        node_id, "src_path", src_path
                    )
                # Preserve provenance and proxy markers for uncertainty/merge.
                self.state.graph_manager.update_node_attribute(
                    node_id,
                    "proxy_target",
                    src_hint or dep_info.get("source"),
                )
                self.state.graph_manager.update_node_attribute(
                    node_id,
                    "proxied_type",
                    prior_type,
                )
                self.state.graph_manager.update_node_attribute(
                    node_id,
                    "is_proxy",
                    True,
                )
                if dep_info.get("name"):
                    self.state.graph_manager.update_node_attribute(
                        node_id, "name", dep_info.get("name")
                    )
                resolved_version = dep_info.get("resolved_version") or dep_info.get("version")
                if resolved_version:
                    self.state.graph_manager.update_node_attribute(
                        node_id, "version", resolved_version
                    )
                self.state.graph_manager.update_node_attribute(
                    node_id, "origin", "external"
                )
                self.state.graph_manager.update_node_attribute(
                    node_id,
                    "provenance",
                    attrs.get("provenance") or "dependency_resolution",
                )
                logger.debug(
                    "Updated node %s -> child graph %s (prior_type=%s)",
                    node_id,
                    dep_graph_id,
                    prior_type,
                )
            except ValueError:
                logger.warning("Node %s no longer exists, skipping update", node_id)

    def _clear_global_dag_dependencies(self) -> None:
        """Clear GlobalDAG dependencies when no deps found."""
        if not self.state.graph_id:
            return

        try:
            dag = GlobalDAG.get_instance(cache_dir=self.state.graph_cache_root)
            dag.replace_dependencies(self.state.graph_id, set())
            logger.info("Cleared GlobalDAG dependencies for %s", self.state.graph_id)
        except NetworkXNoCycle:
            logger.debug("GlobalDAG had no cycle to clear for %s", self.state.graph_id)
        except _SAFE_EXCEPTIONS as e:
            logger.error(
                "Failed to clear GlobalDAG for %s: %s", self.state.graph_id, e
            )

    def _update_global_dag(self, child_graph_ids: List[str]) -> None:
        """Update GlobalDAG with dependency relationships."""
        if not self.state.graph_id or not child_graph_ids:
            return

        try:
            dag = GlobalDAG.get_instance(cache_dir=self.state.graph_cache_root)
            dag.replace_dependencies(self.state.graph_id, set(child_graph_ids))
            logger.info(
                "Updated GlobalDAG for %s: %d children",
                self.state.graph_id,
                len(child_graph_ids),
            )
        except NetworkXNoCycle:
            logger.debug("GlobalDAG update for %s encountered no cycle", self.state.graph_id)
        except _SAFE_EXCEPTIONS as e:
            logger.error(
                "Failed to update GlobalDAG for %s: %s", self.state.graph_id, e
            )
