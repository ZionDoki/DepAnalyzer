"""C/C++ (CMake-based) ecosystem configuration model.

This module defines a wrapper dataclass that groups together the
per-phase configuration slices used by the cpp ecosystem: detect,
parser, code_parser, linker.

The underlying dataclasses live in `depanalyzer.runtime.graph_config`
and are reused here so that the cpp parser package can own its
configuration semantics while sharing the common model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from depanalyzer.runtime.graph_config import (
    CMakeDetectConfig,
    CMakeParserConfig,
    CppCodeParserConfig,
    CppLinkerConfig,
)


@dataclass
class CppEcosystemConfig:
    """Aggregate configuration for the cpp ecosystem."""

    detect: CMakeDetectConfig
    parser: CMakeParserConfig
    code_parser: CppCodeParserConfig
    linker: CppLinkerConfig

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CppEcosystemConfig":
        """Build CppEcosystemConfig from a plain dict.

        Expected mapping shape:

            [ecosystems.cpp.detect]
            ignore_build_dirs = true
            ignore_third_party_dirs = false

            [ecosystems.cpp.parser]
            ignore_object_library_targets = false
            ignore_interface_library_targets = false

            [ecosystems.cpp.code_parser]
            enable_experimental_features = false

            [ecosystems.cpp.linker]
            enable_linkage_enrichment = true
        """
        detect_data = data.get("detect", {}) or {}
        parser_data = data.get("parser", {}) or {}
        code_parser_data = data.get("code_parser", {}) or {}
        linker_data = data.get("linker", {}) or {}

        base_detect = CMakeDetectConfig()
        detect_cfg = CMakeDetectConfig(
            ignore_build_dirs=bool(
                detect_data.get("ignore_build_dirs", base_detect.ignore_build_dirs)
            ),
            ignore_third_party_dirs=bool(
                detect_data.get(
                    "ignore_third_party_dirs", base_detect.ignore_third_party_dirs
                )
            ),
        )

        base_parser = CMakeParserConfig()
        parser_cfg = CMakeParserConfig(
            ignore_object_library_targets=bool(
                parser_data.get(
                    "ignore_object_library_targets",
                    base_parser.ignore_object_library_targets,
                )
            ),
            ignore_interface_library_targets=bool(
                parser_data.get(
                    "ignore_interface_library_targets",
                    base_parser.ignore_interface_library_targets,
                )
            ),
        )

        base_code = CppCodeParserConfig()
        code_parser_cfg = CppCodeParserConfig(
            enable_experimental_features=bool(
                code_parser_data.get(
                    "enable_experimental_features",
                    base_code.enable_experimental_features,
                )
            ),
        )

        base_linker = CppLinkerConfig()
        linker_cfg = CppLinkerConfig(
            enable_linkage_enrichment=bool(
                linker_data.get(
                    "enable_linkage_enrichment",
                    base_linker.enable_linkage_enrichment,
                )
            ),
        )

        return cls(
            detect=detect_cfg,
            parser=parser_cfg,
            code_parser=code_parser_cfg,
            linker=linker_cfg,
        )


__all__ = ["CppEcosystemConfig"]

