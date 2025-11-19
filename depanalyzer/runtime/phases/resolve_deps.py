"""
RESOLVE_DEPS phase implementation.

This phase discovers dependencies, fetches them, and spawns child
transactions in parallel to analyze third-party dependencies.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List

from networkx.exception import NetworkXNoCycle

from depanalyzer.graph.global_dag import GlobalDAG
from depanalyzer.graph.schema import NodeType
from depanalyzer.parsers.base import DependencySpec
from depanalyzer.runtime.coordinator import TransactionCoordinator
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
        dep_specs: List[DependencySpec] = []

        # Method 1: Get from DependencyCollector (event-driven)
        if self.state.dependency_collector:
            discovered = self.state.dependency_collector.get_discovered_dependencies()
            dep_specs.extend(discovered)
            logger.debug("Discovered %d dependencies from collector", len(discovered))

        # Method 2: Scan GraphManager for external dependency nodes
        if self.state.graph_manager:
            for _node_id, node_attrs in self.state.graph_manager.nodes():
                raw_type = node_attrs.get("type") or node_attrs.get("node_type")
                if isinstance(raw_type, NodeType):
                    normalized_type = raw_type.value
                else:
                    normalized_type = raw_type

                if normalized_type in (
                    NodeType.EXTERNAL_DEP.value,
                    NodeType.EXTERNAL_LIBRARY.value,
                ):
                    ecosystem = node_attrs.get("ecosystem")
                    name = node_attrs.get("name")
                    version = node_attrs.get("version")
                    if ecosystem and name:
                        spec = DependencySpec(
                            ecosystem=ecosystem,
                            name=name,
                            version=version or "*",
                        )
                        if spec not in dep_specs:
                            dep_specs.append(spec)

        return dep_specs

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
            except _SAFE_EXCEPTIONS:
                pass

            filtered.append(dep)

        return filtered

    def _process_child_transactions(
        self, successful_deps: List[Dict[str, Any]]
    ) -> List[str]:
        """Create child transactions and collect their graph IDs."""
        # Get coordinator and factory
        coordinator = TransactionCoordinator.get_instance()
        factory = self.state.child_transaction_factory

        if not factory:
            logger.error(
                "No TransactionFactory available, cannot create child transactions"
            )
            return []

        # Submit child transactions
        futures = []
        for dep in successful_deps:
            logger.info("Creating child transaction for: %s", dep["name"])

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
            )

            future = coordinator.submit(child_tx)
            futures.append((dep, future, child_tx.transaction_id))

        # Wait for completion
        logger.info("Waiting for %d child transactions", len(futures))
        child_graph_ids = []
        completed, failed = 0, 0

        for dep, future, child_tx_id in futures:
            try:
                result = future.result(timeout=600)

                if result.success:
                    completed += 1
                    logger.info(
                        "Child %s completed: %s (%d nodes, %d edges)",
                        child_tx_id,
                        dep["name"],
                        result.node_count,
                        result.edge_count,
                    )

                    # Link child graph
                    self._link_dependency_graph(result.graph_id, dep)
                    if result.graph_id:
                        child_graph_ids.append(result.graph_id)
                else:
                    failed += 1
                    logger.error("Child %s failed: %s", child_tx_id, result.error)

            except _SAFE_EXCEPTIONS as e:
                failed += 1
                logger.error("Failed to get result for child %s: %s", child_tx_id, e)

        logger.info("Resolution completed: %d succeeded, %d failed", completed, failed)
        return child_graph_ids

    def _link_dependency_graph(
        self, dep_graph_id: str, dep_info: Dict[str, Any]
    ) -> None:
        """Link dependency graph into parent graph (creates proxy nodes)."""
        if not self.state.graph_manager or not dep_graph_id:
            return

        # This is a simplified version - the full implementation would create
        # proxy nodes and edges to represent the dependency relationship
        logger.debug("Linking dependency graph %s into parent", dep_graph_id)

        # FUTURE ENHANCEMENT: Implement full graph linking logic
        # For now, just log the relationship
        logger.info(
            "Linked dependency: %s (graph_id=%s)", dep_info.get("name"), dep_graph_id
        )

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
