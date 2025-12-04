"""
JOIN phase implementation.

This phase runs ecosystem-specific linkers and join policies
to create cross-file and cross-module edges in the graph.
"""

import logging

from depanalyzer.graph.linker_contract import GlobalContractLinker
from depanalyzer.parsers.registry import EcosystemRegistry
from depanalyzer.runtime.phases.base import BasePhase
from depanalyzer.runtime.context import TransactionContext, JoinContext
from depanalyzer.graph.contract_registry import ContractRegistry
from depanalyzer.runtime.lifecycle import LifecyclePhase

logger = logging.getLogger("depanalyzer.transaction.phase.join")

_SAFE_EXCEPTIONS = (RuntimeError, ValueError, TypeError, AttributeError, KeyError,
                    IndexError, OSError, ImportError, LookupError)


class JoinPhase(BasePhase):
    """
    JOIN phase: Cross-domain wiring and linking.

    This phase:
    1. Runs per-ecosystem linkers to create intra-ecosystem edges
    2. Runs GlobalContractLinker for contract-based linking
    3. Executes additional join policies

    Critical: True - Graph linking is important for complete analysis.
    """

    IS_CRITICAL = True

    def execute(self, context: TransactionContext) -> None:
        """Execute JOIN phase logic."""
        if not self.state.graph_manager:
            logger.warning("GraphManager not initialized, skipping join phase")
            return

        # Run per-ecosystem linkers
        self._run_ecosystem_linkers()

        # Run global contract linker
        self._run_contract_linker()

        # Run additional join policies
        self._run_join_policies()

    def _run_ecosystem_linkers(self) -> None:
        """Run ecosystem-specific linkers."""
        registry = EcosystemRegistry.get_instance()
        ecosystems = registry.list_ecosystems()

        logger.info("Executing linkers for ecosystems: %s", ", ".join(ecosystems))

        for eco in ecosystems:
            linker_cls = registry.get_linker(eco)
            if not linker_cls:
                continue

            try:
                logger.info("Running linker for ecosystem: %s", eco)
                linker_cfg = self.state.graph_build_config.get_linker_config(eco)
                linker = linker_cls(
                    self.state.graph_manager,
                    config=linker_cfg,
                    contract_registry=self.state.contract_registry,
                )
                linker.link()
            except _SAFE_EXCEPTIONS as e:
                logger.error("Linker for %s failed: %s", eco, e, exc_info=True)

    def _run_contract_linker(self) -> None:
        """Run GlobalContractLinker for contract-based edges."""
        try:
            logger.info("Running GlobalContractLinker")
            GlobalContractLinker.link(
                self.state.graph_manager,
                config=self.state.graph_build_config.contract_match,
                contract_registry=self.state.contract_registry,
            )
        except _SAFE_EXCEPTIONS as e:
            logger.error("GlobalContractLinker failed: %s", e, exc_info=True)

    def _run_join_policies(self) -> None:
        """Run additional join policies."""
        if not self.state.join_policies:
            return

        # Create JoinContext
        join_ctx = JoinContext(
            transaction_id=self.state.transaction_id,
            graph_id=self.state.graph_id,
            source=self.state.source,
            workspace=self.state.workspace,
            graph=self.state.graph_manager,
            graph_build_config=self.state.graph_build_config,
            eventbus=self.state.eventbus,
            contract_registry=self.state.contract_registry,
            progress_manager=self.state.progress_manager,
            enable_dependency_resolution=self.state.enable_dependency_resolution,
            max_dependency_depth=self.state.max_dependency_depth,
            max_dependencies=self.state.max_dependencies,
            current_phase=LifecyclePhase.JOIN,
        )

        for strategy in self.state.join_policies:
            try:
                strategy.join(join_ctx)
            except _SAFE_EXCEPTIONS:
                logger.exception("JoinPolicy %r failed", strategy)
