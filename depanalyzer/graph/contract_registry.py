"""Global registry for build interface contracts.

This module provides a singleton registry for managing cross-language build
contracts throughout the analysis lifecycle. Parsers register contracts during
parsing, and hooks match them during the JOIN phase.
"""
from typing import Dict, List, Optional
from collections import defaultdict
import threading

from depanalyzer.runtime.graph_config import ContractMatchConfig

from .contract import BuildInterfaceContract, ContractType


class ContractRegistry:
    """
    Global singleton registry for build interface contracts.

    This registry stores all cross-language contracts discovered during parsing.
    It supports:
    - Registration of provider-only and consumer-only contracts
    - Matching contracts using multiple strategies (Contract/Artifact/Path)
    - Fusion of matched contracts with confidence scoring
    - Thread-safe operations for concurrent parsing

    Usage:
        # During parsing (any parser)
        registry = ContractRegistry()
        registry.register(BuildInterfaceContract(...))

        # During JOIN phase (ContractMatcher hook)
        matched = registry.match_contracts()
        for contract in matched:
            graph.add_edge(contract.consumer_artifact, contract.provider_artifact, ...)
    """

    _instance: Optional["ContractRegistry"] = None
    _lock = threading.Lock()
    _initialized: bool = False

    def __new__(cls) -> "ContractRegistry":
        """Ensure singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    @classmethod
    def get_instance(cls) -> "ContractRegistry":
        """Return the singleton ContractRegistry instance.

        This helper mirrors the pattern used by other global registries
        (for example GraphRegistry) and makes it easier to depend on the
        registry from context construction code.

        Returns:
            ContractRegistry: Global registry instance.
        """
        return cls()

    def __init__(self):
        """Initialize registry (only once)."""
        if self._initialized:
            return

        self._contracts: List[BuildInterfaceContract] = []
        self._provider_map: Dict[str, List[BuildInterfaceContract]] = defaultdict(list)
        self._consumer_map: Dict[str, List[BuildInterfaceContract]] = defaultdict(list)
        self._artifact_name_providers: Dict[str, List[BuildInterfaceContract]] = defaultdict(list)
        self._artifact_name_consumers: Dict[str, List[BuildInterfaceContract]] = defaultdict(list)
        self._initialized = True

    def reset(self):
        """Reset the registry (for testing or multi-project analysis)."""
        with self._lock:
            self._contracts.clear()
            self._provider_map.clear()
            self._consumer_map.clear()
            self._artifact_name_providers.clear()
            self._artifact_name_consumers.clear()

    def register(self, contract: BuildInterfaceContract):
        """
        Register a build interface contract.

        Args:
            contract: Contract to register (can be provider-only, consumer-only, or complete)
        """
        with self._lock:
            self._contracts.append(contract)

            # Index by artifact IDs
            if contract.provider_artifact:
                self._provider_map[contract.provider_artifact].append(contract)
                self._artifact_name_providers[contract.artifact_name].append(contract)

            if contract.consumer_artifact:
                self._consumer_map[contract.consumer_artifact].append(contract)
                self._artifact_name_consumers[contract.artifact_name].append(contract)

    def get_providers(self, artifact_name: Optional[str] = None) -> List[BuildInterfaceContract]:
        """
        Get all provider contracts, optionally filtered by artifact name.

        Args:
            artifact_name: Optional artifact name filter (e.g., "libxxx.so")

        Returns:
            List of provider contracts
        """
        if artifact_name:
            return self._artifact_name_providers.get(artifact_name, [])
        return [c for c in self._contracts if c.provider_artifact]

    def get_consumers(self, artifact_name: Optional[str] = None) -> List[BuildInterfaceContract]:
        """
        Get all consumer contracts, optionally filtered by artifact name.

        Args:
            artifact_name: Optional artifact name filter

        Returns:
            List of consumer contracts
        """
        if artifact_name:
            return self._artifact_name_consumers.get(artifact_name, [])
        return [c for c in self._contracts if c.consumer_artifact]

    def get_all_contracts(self) -> List[BuildInterfaceContract]:
        """Get all registered contracts."""
        return self._contracts.copy()

    def match_contracts(
        self,
        config: Optional[ContractMatchConfig] = None,
    ) -> List[BuildInterfaceContract]:
        """
        Match incomplete contracts to form complete provider-consumer pairs.

        This method uses a multi-strategy approach:
        1. Contract matching (NAPI function names) - confidence 0.95
        2. Artifact name matching - confidence 0.85
        3. Path containment matching - confidence 0.75

        Returns:
            List of complete (matched) contracts, sorted by confidence (descending)
        """
        matched: List[BuildInterfaceContract] = []

        # Decide which matching strategies are enabled.
        cfg = config or ContractMatchConfig()

        # Collect all incomplete contracts
        consumers = [c for c in self._contracts if c.is_consumer_only]

        # Already complete contracts (registered as complete)
        matched.extend([c for c in self._contracts if c.is_complete])

        # Try to match incomplete contracts
        used_providers = set()
        used_consumers = set()

        # Strategy 1: Artifact name matching
        if cfg.enable_artifact_name:
            for consumer in consumers:
                if id(consumer) in used_consumers:
                    continue

                for provider in self._artifact_name_providers.get(consumer.artifact_name, []):
                    if id(provider) in used_providers:
                        continue
                    if not provider.is_provider_only:
                        continue

                    # Found a match by artifact name
                    try:
                        merged = consumer.merge_with(provider)
                        matched.append(merged)
                        used_providers.add(id(provider))
                        used_consumers.add(id(consumer))
                        break
                    except ValueError:
                        continue

        # Sort by confidence (descending)
        matched.sort(key=lambda c: c.confidence, reverse=True)
        return matched

    def find_provider_by_artifact_id(self, artifact_id: str) -> Optional[BuildInterfaceContract]:
        """
        Find a provider contract by its artifact ID.

        Args:
            artifact_id: Normalized artifact path (e.g., "//native/build/libxxx.so")

        Returns:
            Provider contract or None
        """
        contracts = self._provider_map.get(artifact_id, [])
        return contracts[0] if contracts else None

    def find_consumer_by_artifact_id(self, artifact_id: str) -> Optional[BuildInterfaceContract]:
        """
        Find a consumer contract by its artifact ID.

        Args:
            artifact_id: Normalized artifact path

        Returns:
            Consumer contract or None
        """
        contracts = self._consumer_map.get(artifact_id, [])
        return contracts[0] if contracts else None

    def get_statistics(self) -> Dict[str, int]:
        """
        Get registry statistics.

        Returns:
            Dictionary with counts of different contract types
        """
        return {
            "total": len(self._contracts),
            "complete": sum(1 for c in self._contracts if c.is_complete),
            "provider_only": sum(1 for c in self._contracts if c.is_provider_only),
            "consumer_only": sum(1 for c in self._contracts if c.is_consumer_only),
            "by_type": {
                ct.value: sum(1 for c in self._contracts if c.contract_type == ct)
                for ct in ContractType
            }
        }

    def __repr__(self) -> str:
        """Human-readable representation."""
        stats = self.get_statistics()
        return (f"ContractRegistry(total={stats['total']}, "
                f"complete={stats['complete']}, "
                f"provider_only={stats['provider_only']}, "
                f"consumer_only={stats['consumer_only']})")
