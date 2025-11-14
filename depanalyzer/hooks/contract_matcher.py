"""Contract-based cross-language dependency matcher hook.

This hook uses the contract registry to match provider and consumer artifacts
across different build systems (Hvigor + CMake, etc.) using multiple strategies:
1. Contract matching (NAPI function names) - confidence 0.95
2. Artifact name matching - confidence 0.85
3. Path containment matching - confidence 0.75
"""

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Set

from depanalyzer.graph.contract import BuildInterfaceContract, ContractType
from depanalyzer.graph.contract_registry import ContractRegistry
from depanalyzer.graph.manager import EdgeKind, GraphManager, NodeType
from depanalyzer.runtime.eventbus import EventBus

logger = logging.getLogger("depanalyzer.hooks.contract_matcher")


class ContractMatcher:
    """Hook for matching cross-language build contracts and creating dependency edges.

    This hook operates during the JOIN phase, after all parsers have registered
    their contracts. It uses multiple strategies to match providers and consumers
    with confidence scoring.
    """

    def __init__(self, graph_manager: GraphManager, eventbus: EventBus) -> None:
        """Initialize contract matcher hook.

        Args:
            graph_manager: Transaction graph manager
            eventbus: Event bus (not used currently, but kept for interface compatibility)
        """
        self.graph_manager = graph_manager
        self.eventbus = eventbus
        self.registry = ContractRegistry()

        logger.info("ContractMatcher hook initialized")

    def execute(self) -> None:
        """Execute contract matching during JOIN phase.

        This method:
        1. Retrieves all registered contracts from the registry
        2. Matches incomplete contracts using multiple strategies
        3. Creates graph edges for matched contracts
        """
        logger.info("Executing ContractMatcher hook")

        # Get registry statistics
        stats = self.registry.get_statistics()
        logger.info("Contract registry stats: %s", stats)

        if stats["total"] == 0:
            logger.info("No contracts registered, skipping matching")
            return

        # Match contracts
        matched_contracts = self.registry.match_contracts()
        logger.info("Matched %d complete contracts", len(matched_contracts))

        # Create edges for matched contracts
        edges_created = 0
        for contract in matched_contracts:
            if contract.is_complete:
                self._create_contract_edge(contract)
                edges_created += 1

        logger.info("Created %d cross-language dependency edges", edges_created)

    def _create_contract_edge(self, contract: BuildInterfaceContract) -> None:
        """Create graph edges for a matched contract.

        Args:
            contract: Complete contract with both provider and consumer
        """
        provider_id = contract.provider_artifact
        consumer_id = contract.consumer_artifact

        # Ensure both nodes exist (they should be created by parsers)
        if not self.graph_manager.has_node(provider_id):
            logger.warning("Provider node does not exist: %s", provider_id)
            # Create a placeholder node
            self.graph_manager.add_node(
                provider_id,
                NodeType.ARTIFACT,
                confidence=contract.confidence,
                evidence=contract.evidence,
                parser_name="contract_matcher",
                artifact_name=contract.artifact_name,
            )

        if not self.graph_manager.has_node(consumer_id):
            logger.warning("Consumer node does not exist: %s", consumer_id)
            # Create a placeholder node
            self.graph_manager.add_node(
                consumer_id,
                NodeType.ARTIFACT,
                confidence=contract.confidence,
                evidence=contract.evidence,
                parser_name="contract_matcher",
                artifact_name=contract.artifact_name,
            )

        # Create DEPENDS_ON edge from consumer to provider
        self.graph_manager.add_edge(
            source=consumer_id,
            target=provider_id,
            edge_kind=EdgeKind.DEPENDS_ON,
            confidence=contract.confidence,
            evidence=contract.evidence,
            parser_name="contract_matcher",
            contract_type=contract.contract_type.value,
            derived_from="build_contract",
        )

        logger.debug(
            "Created contract edge: %s -> %s (type=%s, conf=%.2f)",
            consumer_id,
            provider_id,
            contract.contract_type.value,
            contract.confidence,
        )

        # If interface/impl files are specified, create additional file-level edges
        if contract.interface_files and contract.impl_files:
            self._create_file_level_edges(contract)

    def _create_file_level_edges(self, contract: BuildInterfaceContract) -> None:
        """Create file-level IMPLEMENTS edges for contract interface/implementation files.

        Args:
            contract: Contract with interface and implementation files
        """
        # Create edges from each interface file to each implementation file
        # (Typically there's a 1:1 or 1:N relationship)
        for interface_file in contract.interface_files:
            for impl_file in contract.impl_files:
                # Check if nodes exist
                if not self.graph_manager.has_node(interface_file):
                    logger.debug("Interface file node not found: %s", interface_file)
                    continue

                if not self.graph_manager.has_node(impl_file):
                    logger.debug("Implementation file node not found: %s", impl_file)
                    continue

                # Create IMPLEMENTS edge (impl -> interface)
                self.graph_manager.add_edge(
                    source=impl_file,
                    target=interface_file,
                    edge_kind="implements",  # Custom edge kind for contract implementation
                    confidence=contract.confidence,
                    evidence=contract.evidence + [f"contract:{contract.contract_type.value}"],
                    parser_name="contract_matcher",
                    derived_from="contract_binding",
                )

                logger.debug(
                    "Created file-level edge: %s implements %s",
                    impl_file,
                    interface_file,
                )


def register_hook(graph_manager: GraphManager, eventbus: EventBus) -> ContractMatcher:
    """Factory function to create and register the ContractMatcher hook.

    Args:
        graph_manager: Transaction graph manager
        eventbus: Event bus

    Returns:
        Initialized ContractMatcher instance
    """
    return ContractMatcher(graph_manager, eventbus)
