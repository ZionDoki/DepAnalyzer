"""
ANALYZE phase implementation.

This phase runs analysis strategies on the completed graph,
including asset projection, deadcode detection, and linkage analysis.
"""

import logging

from depanalyzer.analysis.deadcode import DeadcodeAnalyzer
from depanalyzer.analysis.linkage import LinkageAnalyzer
from depanalyzer.analysis.uncertainty import UncertaintyAnalyzer
from depanalyzer.graph.contract_registry import ContractRegistry
from depanalyzer.runtime.lifecycle import LifecyclePhase
from depanalyzer.runtime.phases.base import BasePhase
from depanalyzer.runtime.context import AnalyzeContext, TransactionContext
from depanalyzer.runtime.strategies import ProjectionContext

logger = logging.getLogger("depanalyzer.transaction.phase.analyze")

_SAFE_EXCEPTIONS = (RuntimeError, ValueError, TypeError, AttributeError, KeyError,
                    IndexError, OSError, ImportError, LookupError)


class AnalyzePhase(BasePhase):
    """
    ANALYZE phase: Run analysis strategies on the graph.

    This phase:
    1. Derives asset-to-artifact projection
    2. Runs deadcode analysis
    3. Runs linkage type analysis
    4. Runs uncertainty analysis
    5. Executes additional analyze strategies

    Critical: True - Analysis is a core part of the transaction.
    """

    IS_CRITICAL = True

    def execute(self, context: TransactionContext) -> None:
        """Execute ANALYZE phase logic."""
        if not self.state.graph_manager:
            logger.warning("GraphManager not initialized, skipping analyze phase")
            return

        # 1. Asset-artifact projection
        self._run_asset_projection()

        # 2. Deadcode analysis
        self._run_deadcode_analysis()

        # 3. Linkage analysis
        self._run_linkage_analysis()

        # 4. Uncertainty analysis
        self._run_uncertainty_analysis()

        # 5. Additional analyze strategies
        self._run_analyze_strategies()

    def _run_asset_projection(self) -> None:
        """Derive asset-to-artifact projection."""
        logger.info("Deriving asset-to-artifact projection")
        try:
            projection_ctx = ProjectionContext(
                transaction_ctx=self._create_transaction_context(),
                graph=self.state.graph_manager,
                projection_config=self.state.graph_build_config.projection,
            )
            self.state.asset_projection_strategy.project(projection_ctx)
            logger.info("Asset-to-artifact projection completed")
        except _SAFE_EXCEPTIONS as e:
            logger.error("Failed to derive projection: %s", e, exc_info=True)

    def _run_deadcode_analysis(self) -> None:
        """Run deadcode analysis."""
        logger.info("Running deadcode analysis")
        try:
            dc_analyzer = DeadcodeAnalyzer(self.state.graph_manager)
            dead_nodes = dc_analyzer.analyze()

            # Store results in graph metadata
            self.state.graph_manager.set_metadata("dead_nodes", list(dead_nodes))
            logger.info("Identified %d dead nodes", len(dead_nodes))
        except _SAFE_EXCEPTIONS as e:
            logger.error("Deadcode analysis failed: %s", e, exc_info=True)

    def _run_linkage_analysis(self) -> None:
        """Run linkage type analysis."""
        logger.info("Running linkage analysis")
        try:
            linkage_analyzer = LinkageAnalyzer(self.state.graph_manager)
            linkage_map = linkage_analyzer.analyze()

            # Store results in graph metadata
            self.state.graph_manager.set_metadata("linkage_map", linkage_map)
            logger.info("Analyzed %d linkage edges", len(linkage_map))
        except _SAFE_EXCEPTIONS as e:
            logger.error("Linkage analysis failed: %s", e, exc_info=True)

    def _run_uncertainty_analysis(self) -> None:
        """Run uncertainty analysis."""
        logger.info("Running uncertainty analysis")
        try:
            # Use the uncertainty config directly from graph_build_config
            uncertainty_analyzer = UncertaintyAnalyzer(
                self.state.graph_manager,
                config=self.state.graph_build_config.uncertainty
            )
            uncertainty_analyzer.analyze()

            uncertainty_edges = self._collect_uncertain_edges()
            if uncertainty_edges:
                self.state.graph_manager.set_metadata("uncertainty_edges", uncertainty_edges)
                logger.info("Identified %d uncertain edges", len(uncertainty_edges))
            else:
                logger.info("Uncertainty analysis completed with no uncertain edges")
        except _SAFE_EXCEPTIONS as e:
            logger.error("Uncertainty analysis failed: %s", e, exc_info=True)

    def _run_analyze_strategies(self) -> None:
        """Run additional analyze strategies."""
        if not self.state.analyze_strategies:
            return

        analyze_ctx = AnalyzeContext(
            transaction_id=self.state.transaction_id,
            graph_id=self.state.graph_id,
            source=self.state.source,
            workspace=self.state.workspace,
            graph=self.state.graph_manager,
            graph_build_config=self.state.graph_build_config,
            eventbus=self.state.eventbus,
            contract_registry=ContractRegistry.get_instance(),
            progress_manager=self.state.progress_manager,
            enable_dependency_resolution=self.state.enable_dependency_resolution,
            max_dependency_depth=self.state.max_dependency_depth,
            max_dependencies=self.state.max_dependencies,
            current_phase=LifecyclePhase.ANALYZE,
        )

        for strategy in self.state.analyze_strategies:
            try:
                strategy.analyze(analyze_ctx)
            except _SAFE_EXCEPTIONS:
                logger.exception("AnalyzeStrategy %r failed", strategy)

    def _create_transaction_context(self) -> TransactionContext:
        """Create TransactionContext for projection."""
        return TransactionContext(
            transaction_id=self.state.transaction_id,
            graph_id=self.state.graph_id,
            source=self.state.source,
            workspace=self.state.workspace,
            graph=self.state.graph_manager,
            graph_build_config=self.state.graph_build_config,
            eventbus=self.state.eventbus,
            contract_registry=ContractRegistry.get_instance(),
            progress_manager=self.state.progress_manager,
            enable_dependency_resolution=self.state.enable_dependency_resolution,
            max_dependency_depth=self.state.max_dependency_depth,
            max_dependencies=self.state.max_dependencies,
            current_phase=LifecyclePhase.ANALYZE,
        )

    def _collect_uncertain_edges(self) -> list[dict]:
        """Collect edges marked as uncertain after the analyzer runs."""
        uncertain_edges = []
        if not self.state.graph_manager:
            return uncertain_edges
        try:
            for source, target, key, attrs in self.state.graph_manager.edges():
                category = attrs.get("uncertainty_category")
                if category and category != "definite":
                    uncertain_edges.append({
                        "source": source,
                        "target": target,
                        "key": key,
                        "category": category,
                        "reasons": list(attrs.get("uncertainty_reasons") or []),
                    })
        except _SAFE_EXCEPTIONS as exc:
            logger.debug("Failed to collect uncertain edges: %s", exc)
        return uncertain_edges
