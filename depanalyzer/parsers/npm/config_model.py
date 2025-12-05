"""NPM ecosystem configuration model.

This module defines the configuration dataclasses for the npm ecosystem,
grouping together per-phase configuration slices: detect, parser,
code_parser, and linker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class NpmDetectConfig:
    """Detection options for the npm ecosystem."""

    ignore_node_modules: bool = True
    detect_workspaces: bool = True
    extra_ignore_patterns: List[str] = field(default_factory=list)


@dataclass
class NpmParserConfig:
    """Config options for npm package.json parser."""

    include_dev_dependencies: bool = False
    include_peer_dependencies: bool = False
    parse_lock_file: bool = True


@dataclass
class NpmCodeParserConfig:
    """Config for JavaScript/TypeScript code parser."""

    parse_typescript: bool = True
    # Maximum levels to search for ancestor index files
    max_import_ancestor_levels: int = 8


@dataclass
class NpmLinkerConfig:
    """Config for npm linker behavior."""

    link_workspace_packages: bool = True
    infer_entry_from_main: bool = True
    enable_native_addon_link: bool = True


@dataclass
class NpmEcosystemConfig:
    """Aggregate configuration for npm ecosystem.

    Attributes mirror the GraphBuildConfig slices but are grouped per
    ecosystem so they can be constructed from a single TOML/JSON
    section: [ecosystems.npm.*]
    """

    detect: NpmDetectConfig
    parser: NpmParserConfig
    code_parser: NpmCodeParserConfig
    linker: NpmLinkerConfig

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NpmEcosystemConfig":
        """Build NpmEcosystemConfig from a plain dict.

        The expected mapping shape is:

            [ecosystems.npm.detect]
            ignore_node_modules = true
            detect_workspaces = true

            [ecosystems.npm.parser]
            include_dev_dependencies = false
            parse_lock_file = true

            [ecosystems.npm.code_parser]
            parse_typescript = true

            [ecosystems.npm.linker]
            link_workspace_packages = true
            enable_native_addon_link = true

        Args:
            data: Configuration dictionary.

        Returns:
            NpmEcosystemConfig instance.
        """
        detect_data = data.get("detect", {}) or {}
        parser_data = data.get("parser", {}) or {}
        code_parser_data = data.get("code_parser", {}) or {}
        linker_data = data.get("linker", {}) or {}

        # Build detect config
        base_detect = NpmDetectConfig()
        detect_cfg = NpmDetectConfig(
            ignore_node_modules=bool(
                detect_data.get("ignore_node_modules", base_detect.ignore_node_modules)
            ),
            detect_workspaces=bool(
                detect_data.get("detect_workspaces", base_detect.detect_workspaces)
            ),
            extra_ignore_patterns=list(
                detect_data.get("extra_ignore_patterns", base_detect.extra_ignore_patterns)
            ),
        )

        # Build parser config
        base_parser = NpmParserConfig()
        parser_cfg = NpmParserConfig(
            include_dev_dependencies=bool(
                parser_data.get(
                    "include_dev_dependencies", base_parser.include_dev_dependencies
                )
            ),
            include_peer_dependencies=bool(
                parser_data.get(
                    "include_peer_dependencies", base_parser.include_peer_dependencies
                )
            ),
            parse_lock_file=bool(
                parser_data.get("parse_lock_file", base_parser.parse_lock_file)
            ),
        )

        # Build code parser config
        base_code = NpmCodeParserConfig()
        code_parser_cfg = NpmCodeParserConfig(
            parse_typescript=bool(
                code_parser_data.get("parse_typescript", base_code.parse_typescript)
            ),
            max_import_ancestor_levels=int(
                code_parser_data.get(
                    "max_import_ancestor_levels", base_code.max_import_ancestor_levels
                )
            ),
        )

        # Build linker config
        base_linker = NpmLinkerConfig()
        linker_cfg = NpmLinkerConfig(
            link_workspace_packages=bool(
                linker_data.get(
                    "link_workspace_packages", base_linker.link_workspace_packages
                )
            ),
            infer_entry_from_main=bool(
                linker_data.get(
                    "infer_entry_from_main", base_linker.infer_entry_from_main
                )
            ),
            enable_native_addon_link=bool(
                linker_data.get(
                    "enable_native_addon_link", base_linker.enable_native_addon_link
                )
            ),
        )

        return cls(
            detect=detect_cfg,
            parser=parser_cfg,
            code_parser=code_parser_cfg,
            linker=linker_cfg,
        )


__all__ = [
    "NpmEcosystemConfig",
    "NpmDetectConfig",
    "NpmParserConfig",
    "NpmCodeParserConfig",
    "NpmLinkerConfig",
]
