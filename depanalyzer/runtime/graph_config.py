"""Graph build configuration models.

This module defines `GraphBuildConfig` and related configuration data
classes that control how a transaction builds a dependency construct
graph across all lifecycle phases:

    Acquire → Detect → Parse → ResolveDeps → Join → Analyze → Export

The configuration is intentionally generic and ecosystem‑agnostic. Each
ecosystem (e.g., cpp, hvigor) may consume the parts that are relevant
to its detectors, parsers, linkers, and code parsers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from depanalyzer.graph.projection_config import ProjectionConfig
from depanalyzer.runtime.ecosystem_config_registry import EcosystemConfigRegistry


# ---------------------------------------------------------------------------
# Detect configuration
# ---------------------------------------------------------------------------


@dataclass
class HvigorDetectConfig:
    """Detection options for the Hvigor ecosystem."""

    include_root_dirs: Optional[list[str]] = None
    ignore_node_modules: bool = True


@dataclass
class CMakeDetectConfig:
    """Detection options for the C/CMake ecosystem."""

    ignore_build_dirs: bool = True
    ignore_third_party_dirs: bool = False


@dataclass
class DetectConfig:
    """Top‑level detection configuration.

    Provides per‑ecosystem configuration slices that detectors can
    consume. New ecosystems can extend this model as needed.
    """

    hvigor: HvigorDetectConfig = field(default_factory=HvigorDetectConfig)
    cpp: CMakeDetectConfig = field(default_factory=CMakeDetectConfig)

    def for_ecosystem(self, ecosystem: str) -> Optional[Any]:
        """Return config slice for a given ecosystem, if available."""
        if ecosystem == "hvigor":
            return self.hvigor
        if ecosystem == "cpp":
            return self.cpp
        return None


# ---------------------------------------------------------------------------
# Parser configuration
# ---------------------------------------------------------------------------


@dataclass
class HvigorParserConfig:
    """Config options for Hvigor config parser."""

    enable_native_dependencies: bool = True
    infer_missing_modules: bool = True


@dataclass
class CMakeParserConfig:
    """Config options for CMake config parser."""

    ignore_object_library_targets: bool = False
    ignore_interface_library_targets: bool = False


@dataclass
class ParserConfig:
    """Top‑level config for configuration parsers."""

    hvigor: HvigorParserConfig = field(default_factory=HvigorParserConfig)
    cpp: CMakeParserConfig = field(default_factory=CMakeParserConfig)

    def for_ecosystem(self, ecosystem: str) -> Optional[Any]:
        """Return parser config slice for a given ecosystem."""
        if ecosystem == "hvigor":
            return self.hvigor
        if ecosystem == "cpp":
            return self.cpp
        return None


# ---------------------------------------------------------------------------
# Code parser configuration
# ---------------------------------------------------------------------------


@dataclass
class HvigorCodeParserConfig:
    """Config for ArkTS/Hvigor code parser."""

    max_import_ancestor_levels: int = 8


@dataclass
class CppCodeParserConfig:
    """Config for C/C++ code parser.

    Currently acts as a placeholder for future options.
    """

    enable_experimental_features: bool = False


@dataclass
class CodeParserConfig:
    """Top‑level configuration for process‑pool code parsers."""

    hvigor: HvigorCodeParserConfig = field(default_factory=HvigorCodeParserConfig)
    cpp: CppCodeParserConfig = field(default_factory=CppCodeParserConfig)

    def for_ecosystem(self, ecosystem: str) -> Optional[Any]:
        """Return code‑parser config slice for a given ecosystem."""
        if ecosystem == "hvigor":
            return self.hvigor
        if ecosystem == "cpp":
            return self.cpp
        return None


# ---------------------------------------------------------------------------
# Linker configuration
# ---------------------------------------------------------------------------


@dataclass
class HvigorLinkerConfig:
    """Config for Hvigor linker behavior."""

    attach_code_without_module: bool = False
    auto_infer_entry_module: bool = False
    enable_path_bridge: bool = True
    enable_shared_library_cpp_link: bool = True


@dataclass
class CppLinkerConfig:
    """Config for C++ linker behavior."""

    enable_linkage_enrichment: bool = True


@dataclass
class LinkerConfig:
    """Top‑level configuration for per‑ecosystem linkers."""

    hvigor: HvigorLinkerConfig = field(default_factory=HvigorLinkerConfig)
    cpp: CppLinkerConfig = field(default_factory=CppLinkerConfig)

    def for_ecosystem(self, ecosystem: str) -> Optional[Any]:
        """Return linker config slice for a given ecosystem."""
        if ecosystem == "hvigor":
            return self.hvigor
        if ecosystem == "cpp":
            return self.cpp
        return None


# ---------------------------------------------------------------------------
# Contract matching configuration
# ---------------------------------------------------------------------------


@dataclass
class ContractMatchConfig:
    """Configuration for build interface contract matching."""

    enable_artifact_name: bool = True
    enable_napi: bool = False
    enable_extern_c: bool = False
    enable_path: bool = False
    enable_config: bool = False


# ---------------------------------------------------------------------------
# Uncertainty / over-approximation policy configuration
# ---------------------------------------------------------------------------


@dataclass
class UncertaintyPolicyConfig:
    """Configuration for uncertainty classification policy.

    This controls how node/edge confidence values and structural patterns are
    mapped into high-level uncertainty annotations (definite/probable/conditional).
    """

    # Global switch: when disabled, the uncertainty analyzer is skipped.
    enabled: bool = False

    # Confidence thresholds used for default category assignment.
    probable_min_confidence: float = 0.6
    definite_min_confidence: float = 0.9

    # Feature toggles for marking specific structural patterns.
    mark_projection_edges_as_probable: bool = True
    mark_contract_edges_as_probable: bool = True
    mark_proxy_nodes_as_conditional: bool = True
    mark_unresolved_external_as_conditional: bool = True
    mark_dynamic_patterns_as_conditional: bool = True


# ---------------------------------------------------------------------------
# Fallback configuration
# ---------------------------------------------------------------------------


@dataclass
class FallbackConfig:
    """Configuration for fallback graph construction.

    When enabled, a flat tree is added that connects all files (and
    isolated nodes) to a single synthetic node so downstream license
    comparison or analysis can still operate when parsing is incomplete.
    """

    enabled: bool = False
    root_id: str = "//fallback/license_scan"
    include_isolated_nodes: bool = True


@dataclass
class LicenseLinkConfig:
    """Configuration for attaching license files to graph roots."""

    enabled: bool = True
    file_patterns: list[str] = field(
        default_factory=lambda: [
            "LICENSE",
            "LICENSE.*",
            "COPYING",
            "COPYING.*",
            "NOTICE",
            "NOTICE.*",
        ]
    )


# ---------------------------------------------------------------------------
# Aggregate graph build configuration
# ---------------------------------------------------------------------------


@dataclass
class GraphBuildConfig:
    """Aggregate configuration for dependency construct graph building.

    This configuration is attached to a Transaction and sliced per
    lifecycle phase:

    - detect:   controls detectors
    - parser:   controls config parsers
    - code_parser: controls process‑pool code parsers
    - linker:   controls per‑ecosystem linkers
    - projection: controls asset→artifact projection
    - contract_match: controls contract registry matching strategies
    """

    detect: DetectConfig = field(default_factory=DetectConfig)
    parser: ParserConfig = field(default_factory=ParserConfig)
    code_parser: CodeParserConfig = field(default_factory=CodeParserConfig)
    linker: LinkerConfig = field(default_factory=LinkerConfig)
    projection: ProjectionConfig = field(default_factory=ProjectionConfig)
    contract_match: ContractMatchConfig = field(default_factory=ContractMatchConfig)
    uncertainty: UncertaintyPolicyConfig = field(default_factory=UncertaintyPolicyConfig)
    fallback: FallbackConfig = field(default_factory=FallbackConfig)
    license_link: LicenseLinkConfig = field(default_factory=LicenseLinkConfig)

    # Optional per-ecosystem configuration objects provided by parser
    # packages via EcosystemConfigRegistry. When present, they take
    # precedence over the static DetectConfig/ParserConfig/LinkerConfig
    # slices for the corresponding ecosystem.
    ecosystem_configs: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def default(cls) -> "GraphBuildConfig":
        """Return a GraphBuildConfig instance with default values."""
        return cls()

    # ------------------------------------------------------------------
    # Helpers for resolving effective per-ecosystem configuration
    # ------------------------------------------------------------------

    def get_detect_config(self, ecosystem: str) -> Optional[Any]:
        """Return effective detect configuration for an ecosystem."""
        eco_cfg = self.ecosystem_configs.get(ecosystem)
        if eco_cfg is not None and hasattr(eco_cfg, "detect"):
            return getattr(eco_cfg, "detect")
        return self.detect.for_ecosystem(ecosystem)

    def get_parser_config(self, ecosystem: str) -> Optional[Any]:
        """Return effective parser configuration for an ecosystem."""
        eco_cfg = self.ecosystem_configs.get(ecosystem)
        if eco_cfg is not None and hasattr(eco_cfg, "parser"):
            return getattr(eco_cfg, "parser")
        return self.parser.for_ecosystem(ecosystem)

    def get_code_parser_config(self, ecosystem: str) -> Optional[Any]:
        """Return effective code-parser configuration for an ecosystem."""
        eco_cfg = self.ecosystem_configs.get(ecosystem)
        if eco_cfg is not None and hasattr(eco_cfg, "code_parser"):
            return getattr(eco_cfg, "code_parser")
        return self.code_parser.for_ecosystem(ecosystem)

    def get_linker_config(self, ecosystem: str) -> Optional[Any]:
        """Return effective linker configuration for an ecosystem."""
        eco_cfg = self.ecosystem_configs.get(ecosystem)
        if eco_cfg is not None and hasattr(eco_cfg, "linker"):
            return getattr(eco_cfg, "linker")
        return self.linker.for_ecosystem(ecosystem)

    # ------------------------------------------------------------------
    # Loading from user-provided configuration mappings
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GraphBuildConfig":
        """Create GraphBuildConfig from a plain mapping.

        The expected shape roughly matches the TOML/JSON configuration
        documented under docs/dependency_graph_lifecycle_and_config.md:

            [projection]
            ...

            [contract_match]
            ...

            [ecosystems.<eco>.detect]
            ...

        Args:
            data: Parsed configuration dict.

        Returns:
            GraphBuildConfig instance with fields populated from config.
        """
        cfg = cls.default()

        # Projection (language-agnostic)
        proj = data.get("projection", {}) or {}
        if isinstance(proj, dict):
            cfg.projection = ProjectionConfig(
                enable_fallback=proj.get("enable_fallback", cfg.projection.enable_fallback),
                enable_link_closure=proj.get(
                    "enable_link_closure", cfg.projection.enable_link_closure
                ),
                enable_header_closure=proj.get(
                    "enable_header_closure", cfg.projection.enable_header_closure
                ),
                max_header_hops=proj.get(
                    "max_header_hops", cfg.projection.max_header_hops
                ),
                ignore_external_placeholders_in_fallback=proj.get(
                    "ignore_external_placeholders_in_fallback",
                    cfg.projection.ignore_external_placeholders_in_fallback,
                ),
                nearest_dir_min_evidence=proj.get(
                    "nearest_dir_min_evidence", cfg.projection.nearest_dir_min_evidence
                ),
                link_closure_max_hops=proj.get(
                    "link_closure_max_hops", cfg.projection.link_closure_max_hops
                ),
                resolve_include_placeholders=proj.get(
                    "resolve_include_placeholders",
                    cfg.projection.resolve_include_placeholders,
                ),
                fuse_evidence=proj.get("fuse_evidence", cfg.projection.fuse_evidence),
                fallback_max_targets_per_node=proj.get(
                    "fallback_max_targets_per_node",
                    cfg.projection.fallback_max_targets_per_node,
                ),
                fallback_disable_import_inherited=proj.get(
                    "fallback_disable_import_inherited",
                    cfg.projection.fallback_disable_import_inherited,
                ),
                fallback_disable_nearest_dir=proj.get(
                    "fallback_disable_nearest_dir",
                    cfg.projection.fallback_disable_nearest_dir,
                ),
            )

        # Contract matching (language-agnostic)
        cm = data.get("contract_match", {}) or {}
        if isinstance(cm, dict):
            cfg.contract_match = ContractMatchConfig(
                enable_artifact_name=cm.get("enable_artifact_name", True),
                enable_napi=cm.get("enable_napi", False),
                enable_extern_c=cm.get("enable_extern_c", False),
                enable_path=cm.get("enable_path", False),
                enable_config=cm.get("enable_config", False),
            )

        # Uncertainty policy (language-agnostic)
        unc = data.get("uncertainty", {}) or {}
        if isinstance(unc, dict):
            cfg.uncertainty = UncertaintyPolicyConfig(
                enabled=unc.get("enabled", cfg.uncertainty.enabled),
                probable_min_confidence=unc.get(
                    "probable_min_confidence", cfg.uncertainty.probable_min_confidence
                ),
                definite_min_confidence=unc.get(
                    "definite_min_confidence", cfg.uncertainty.definite_min_confidence
                ),
                mark_projection_edges_as_probable=unc.get(
                    "mark_projection_edges_as_probable",
                    cfg.uncertainty.mark_projection_edges_as_probable,
                ),
                mark_contract_edges_as_probable=unc.get(
                    "mark_contract_edges_as_probable",
                    cfg.uncertainty.mark_contract_edges_as_probable,
                ),
                mark_proxy_nodes_as_conditional=unc.get(
                    "mark_proxy_nodes_as_conditional",
                    cfg.uncertainty.mark_proxy_nodes_as_conditional,
                ),
                mark_unresolved_external_as_conditional=unc.get(
                    "mark_unresolved_external_as_conditional",
                    cfg.uncertainty.mark_unresolved_external_as_conditional,
                ),
                mark_dynamic_patterns_as_conditional=unc.get(
                    "mark_dynamic_patterns_as_conditional",
                    cfg.uncertainty.mark_dynamic_patterns_as_conditional,
                ),
            )

        # Per-ecosystem configuration (plugins)
        ecosystems_dict = data.get("ecosystems", {}) or {}
        if isinstance(ecosystems_dict, dict):
            for eco, eco_cfg_data in ecosystems_dict.items():
                if not isinstance(eco_cfg_data, dict):
                    continue
                eco_cfg = EcosystemConfigRegistry.from_dict(eco, eco_cfg_data)
                if eco_cfg is not None:
                    cfg.ecosystem_configs[eco] = eco_cfg

        # Fallback configuration
        fallback_data = data.get("fallback", {}) or {}
        if isinstance(fallback_data, dict):
            cfg.fallback = FallbackConfig(
                enabled=bool(fallback_data.get("enabled", cfg.fallback.enabled)),
                root_id=str(fallback_data.get("root_id", cfg.fallback.root_id)),
                include_isolated_nodes=bool(
                    fallback_data.get(
                        "include_isolated_nodes", cfg.fallback.include_isolated_nodes
                    )
                ),
            )

        license_link_data = data.get("license_link", {}) or {}
        if isinstance(license_link_data, dict):
            patterns = license_link_data.get(
                "file_patterns", cfg.license_link.file_patterns
            )
            cfg.license_link = LicenseLinkConfig(
                enabled=bool(license_link_data.get("enabled", cfg.license_link.enabled)),
                file_patterns=list(patterns)
                if isinstance(patterns, list)
                else cfg.license_link.file_patterns,
            )

        return cfg


__all__ = [
    "GraphBuildConfig",
    "DetectConfig",
    "ParserConfig",
    "CodeParserConfig",
    "LinkerConfig",
    "ContractMatchConfig",
    "UncertaintyPolicyConfig",
    "FallbackConfig",
    "LicenseLinkConfig",
    "HvigorDetectConfig",
    "CMakeDetectConfig",
    "HvigorParserConfig",
    "CMakeParserConfig",
    "HvigorCodeParserConfig",
    "CppCodeParserConfig",
    "HvigorLinkerConfig",
    "CppLinkerConfig",
]
