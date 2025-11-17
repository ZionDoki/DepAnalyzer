"""Global contract-based linker.

This module provides a small, ecosystem-agnostic linker that turns
matched build interface contracts into graph edges between artifacts
and, optionally, between implementation and interface files.
"""

from __future__ import annotations

import logging

from depanalyzer.graph.contract import BuildInterfaceContract
from depanalyzer.graph.contract_registry import ContractRegistry
from depanalyzer.graph.linking import LinkClass
from depanalyzer.graph.manager import EdgeKind, GraphManager, NodeType
from depanalyzer.runtime.graph_config import ContractMatchConfig

logger = logging.getLogger("depanalyzer.graph.linker_contract")


class GlobalContractLinker:
    """Global linker that materializes contract relationships as graph edges."""

    @classmethod
    def link(
        cls,
        graph_manager: GraphManager,
        config: ContractMatchConfig | None = None,
    ) -> None:
        """Apply contract-based linking on the current graph.

        This method:
        1. Retrieves all registered contracts from the registry.
        2. Matches incomplete contracts into provider/consumer pairs.
        3. Creates artifact-level DEPENDS_ON edges for each complete contract.
        4. Optionally creates file-level IMPLEMENTS edges between impl and
           interface files declared in the contract.
        """
        registry = ContractRegistry()
        stats = registry.get_statistics()
        logger.info("GlobalContractLinker: contract registry stats: %s", stats)

        if stats["total"] == 0:
            logger.info("GlobalContractLinker: no contracts registered, skipping")
            return

        matched_contracts = registry.match_contracts(config=config)
        if not matched_contracts:
            logger.info("GlobalContractLinker: no complete contracts matched")
            return

        artifact_edges = 0
        file_edges = 0

        for contract in matched_contracts:
            if not contract.is_complete:
                continue

            cls._ensure_artifact_nodes(graph_manager, contract)
            cls._create_artifact_edge(graph_manager, contract)
            artifact_edges += 1

            file_edges += cls._create_file_level_edges(graph_manager, contract)

        logger.info(
            "GlobalContractLinker: created %d artifact-level and %d file-level edges",
            artifact_edges,
            file_edges,
        )

    @staticmethod
    def _ensure_artifact_nodes(
        graph_manager: GraphManager,
        contract: BuildInterfaceContract,
    ) -> None:
        """Ensure provider and consumer artifact nodes exist."""
        provider_id = contract.provider_artifact
        consumer_id = contract.consumer_artifact

        if provider_id and not graph_manager.has_node(provider_id):
            graph_manager.add_node(
                provider_id,
                NodeType.ARTIFACT,
                parser_name="global_contract_linker",
                artifact_name=contract.artifact_name,
                origin="in_repo",
                provenance="contract_provider",
            )

        if consumer_id and not graph_manager.has_node(consumer_id):
            graph_manager.add_node(
                consumer_id,
                NodeType.ARTIFACT,
                parser_name="global_contract_linker",
                artifact_name=contract.artifact_name,
                origin="in_repo",
                provenance="contract_consumer",
            )

    @staticmethod
    def _create_artifact_edge(
        graph_manager: GraphManager,
        contract: BuildInterfaceContract,
    ) -> None:
        """Create consumer->provider DEPENDS_ON edge for a contract."""
        provider_id = contract.provider_artifact
        consumer_id = contract.consumer_artifact

        if not provider_id or not consumer_id:
            return

        graph_manager.add_edge(
            consumer_id,
            provider_id,
            edge_kind=EdgeKind.DEPENDS_ON.value,
            parser_name="global_contract_linker",
            confidence=contract.confidence,
            evidence=list(contract.evidence),
            contract_type=contract.contract_type.value,
            link_class=LinkClass.BUILD_CONFIG.value,
            derived_from="build_contract",
        )

    @staticmethod
    def _create_file_level_edges(
        graph_manager: GraphManager,
        contract: BuildInterfaceContract,
    ) -> int:
        """Create IMPLEMENTS edges between implementation and interface files.

        Returns:
            int: Number of edges created.
        """
        if not contract.interface_files or not contract.impl_files:
            return 0

        created = 0

        for interface_file in contract.interface_files:
            for impl_file in contract.impl_files:
                if not graph_manager.has_node(interface_file):
                    continue
                if not graph_manager.has_node(impl_file):
                    continue

                graph_manager.add_edge(
                    impl_file,
                    interface_file,
                    edge_kind=EdgeKind.IMPLEMENTS_NATIVE.value,
                    parser_name="global_contract_linker",
                    confidence=contract.confidence,
                    evidence=list(contract.evidence),
                    link_class=LinkClass.BUILD_CONFIG.value,
                    derived_from="contract_binding",
                )
                created += 1

        return created
