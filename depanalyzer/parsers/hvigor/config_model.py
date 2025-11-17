"""Hvigor ecosystem configuration model.

This module defines a small wrapper dataclass that groups together
the per-phase configuration slices used by the Hvigor ecosystem:
detect, parser, code_parser, linker.

The underlying dataclasses are defined in `depanalyzer.runtime.graph_config`
and reused here so that Hvigor can own its configuration semantics while
sharing the common model with GraphBuildConfig.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from depanalyzer.runtime.graph_config import (
    HvigorCodeParserConfig,
    HvigorDetectConfig,
    HvigorLinkerConfig,
    HvigorParserConfig,
)


@dataclass
class HvigorEcosystemConfig:
    """Aggregate configuration for Hvigor ecosystem.

    Attributes mirror the GraphBuildConfig slices but are grouped per
    ecosystem so they can be constructed from a single TOML/JSON
    section: [ecosystems.hvigor.*]
    """

    detect: HvigorDetectConfig
    parser: HvigorParserConfig
    code_parser: HvigorCodeParserConfig
    linker: HvigorLinkerConfig

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HvigorEcosystemConfig":
        """Build HvigorEcosystemConfig from a plain dict.

        The expected mapping shape is:

            [ecosystems.hvigor.detect]
            include_root_dirs = [...]
            ignore_node_modules = true

            [ecosystems.hvigor.parser]
            enable_native_dependencies = true
            ...

            [ecosystems.hvigor.code_parser]
            max_import_ancestor_levels = 8

            [ecosystems.hvigor.linker]
            enable_path_bridge = true
            ...
        """
        detect_data = data.get("detect", {}) or {}
        parser_data = data.get("parser", {}) or {}
        code_parser_data = data.get("code_parser", {}) or {}
        linker_data = data.get("linker", {}) or {}

        base_detect = HvigorDetectConfig()
        detect_cfg = HvigorDetectConfig(
            include_root_dirs=detect_data.get(
                "include_root_dirs", base_detect.include_root_dirs
            ),
            ignore_node_modules=bool(
                detect_data.get("ignore_node_modules", base_detect.ignore_node_modules)
            ),
        )

        base_parser = HvigorParserConfig()
        parser_cfg = HvigorParserConfig(
            enable_native_dependencies=bool(
                parser_data.get(
                    "enable_native_dependencies",
                    base_parser.enable_native_dependencies,
                )
            ),
            infer_missing_modules=bool(
                parser_data.get(
                    "infer_missing_modules", base_parser.infer_missing_modules
                )
            ),
        )

        base_code = HvigorCodeParserConfig()
        code_parser_cfg = HvigorCodeParserConfig(
            max_import_ancestor_levels=int(
                code_parser_data.get(
                    "max_import_ancestor_levels",
                    base_code.max_import_ancestor_levels,
                )
            ),
        )

        base_linker = HvigorLinkerConfig()
        linker_cfg = HvigorLinkerConfig(
            attach_code_without_module=bool(
                linker_data.get(
                    "attach_code_without_module",
                    base_linker.attach_code_without_module,
                )
            ),
            auto_infer_entry_module=bool(
                linker_data.get(
                    "auto_infer_entry_module",
                    base_linker.auto_infer_entry_module,
                )
            ),
            enable_path_bridge=bool(
                linker_data.get("enable_path_bridge", base_linker.enable_path_bridge)
            ),
            enable_shared_library_cpp_link=bool(
                linker_data.get(
                    "enable_shared_library_cpp_link",
                    base_linker.enable_shared_library_cpp_link,
                )
            ),
        )

        return cls(
            detect=detect_cfg,
            parser=parser_cfg,
            code_parser=code_parser_cfg,
            linker=linker_cfg,
        )


__all__ = ["HvigorEcosystemConfig"]

