"""Build interface contracts for cross-language dependency linking.

This module defines the contract data structure that acts as the single source
of truth for connecting mixed-language build artifacts (e.g., Hvigor + CMake).
"""
from dataclasses import dataclass, field
from typing import List, Optional
from enum import Enum


class ContractType(str, Enum):
    """Type of build interface contract."""
    NAPI = "napi"              # NAPI function binding (highest confidence)
    EXTERN_C = "extern_c"      # extern "C" symbol export
    ARTIFACT_NAME = "artifact_name"  # Matching by artifact name (e.g., libxxx.so)
    PATH = "path"              # Path containment relationship
    CONFIG = "config"          # Build configuration declaration


@dataclass
class BuildInterfaceContract:
    """
    Cross-language build interface contract.

    This contract represents the relationship between a provider artifact
    (e.g., CMake-built libxxx.so) and a consumer artifact (e.g., Hvigor
    module expecting libxxx.so).

    All paths use normalized format:
    - Internal: //relative/to/root_path
    - External: //../external/path

    Attributes:
        provider_artifact: Provider artifact node ID (e.g., "//native/build/libxxx.so")
        consumer_artifact: Consumer artifact node ID (e.g., "//module/libs/arm64-v8a/libxxx.so")
        artifact_name: Expected artifact filename (e.g., "libxxx.so")
        contract_type: Type of contract (determines matching strategy)
        confidence: Matching confidence (0.0-1.0)
        evidence: List of evidence strings for traceability
        interface_files: Interface declaration files (e.g., .d.ts files)
        impl_files: Implementation files (e.g., .cpp NAPI files)
        metadata: Additional contract-specific metadata
    """
    # Artifact identities (normalized paths)
    provider_artifact: str
    consumer_artifact: str

    # Contract metadata
    artifact_name: str
    contract_type: ContractType
    confidence: float
    evidence: List[str] = field(default_factory=list)

    # File-level evidence (optional)
    interface_files: List[str] = field(default_factory=list)
    impl_files: List[str] = field(default_factory=list)

    # Additional metadata
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        """Validate contract data."""
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"Confidence must be in [0.0, 1.0], got {self.confidence}")

        if not self.provider_artifact and not self.consumer_artifact:
            raise ValueError("At least one of provider_artifact or consumer_artifact must be set")

    @property
    def is_complete(self) -> bool:
        """Check if both provider and consumer are specified."""
        return bool(self.provider_artifact and self.consumer_artifact)

    @property
    def is_provider_only(self) -> bool:
        """Check if this is a provider-only contract (waiting for consumer match)."""
        return bool(self.provider_artifact and not self.consumer_artifact)

    @property
    def is_consumer_only(self) -> bool:
        """Check if this is a consumer-only contract (waiting for provider match)."""
        return bool(self.consumer_artifact and not self.provider_artifact)

    def merge_with(self, other: 'BuildInterfaceContract') -> 'BuildInterfaceContract':
        """
        Merge with another contract (for matching provider and consumer).

        Args:
            other: Another contract to merge with

        Returns:
            New merged contract with combined evidence

        Raises:
            ValueError: If contracts are incompatible
        """
        if self.artifact_name != other.artifact_name:
            raise ValueError(f"Cannot merge contracts with different artifact names: "
                             f"{self.artifact_name} != {other.artifact_name}")

        # Determine provider and consumer
        provider = self.provider_artifact or other.provider_artifact
        consumer = self.consumer_artifact or other.consumer_artifact

        if not provider or not consumer:
            raise ValueError("Cannot merge: both contracts must provide complementary sides")

        # Use higher confidence and stronger contract type
        confidence = max(self.confidence, other.confidence)
        contract_type = self._stronger_type(self.contract_type, other.contract_type)

        # Merge evidence
        evidence = list(set(self.evidence + other.evidence))

        # Merge file lists
        interface_files = list(set(self.interface_files + other.interface_files))
        impl_files = list(set(self.impl_files + other.impl_files))

        # Merge metadata
        metadata = {**self.metadata, **other.metadata}

        return BuildInterfaceContract(
            provider_artifact=provider,
            consumer_artifact=consumer,
            artifact_name=self.artifact_name,
            contract_type=contract_type,
            confidence=confidence,
            evidence=evidence,
            interface_files=interface_files,
            impl_files=impl_files,
            metadata=metadata
        )

    @staticmethod
    def _stronger_type(type1: ContractType, type2: ContractType) -> ContractType:
        """Determine the stronger contract type (higher confidence)."""
        priority = {
            ContractType.NAPI: 5,
            ContractType.EXTERN_C: 4,
            ContractType.ARTIFACT_NAME: 3,
            ContractType.CONFIG: 2,
            ContractType.PATH: 1,
        }
        return type1 if priority.get(type1, 0) >= priority.get(type2, 0) else type2

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "provider_artifact": self.provider_artifact,
            "consumer_artifact": self.consumer_artifact,
            "artifact_name": self.artifact_name,
            "contract_type": self.contract_type.value,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "interface_files": self.interface_files,
            "impl_files": self.impl_files,
            "metadata": self.metadata,
        }

    def __repr__(self) -> str:
        """Human-readable representation."""
        return (f"Contract({self.contract_type.value}, {self.confidence:.2f}, "
                f"provider={self.provider_artifact or 'TBD'}, "
                f"consumer={self.consumer_artifact or 'TBD'})")
