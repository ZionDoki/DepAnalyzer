"""Maven ecosystem configuration model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class MavenDetectConfig:
    """Detection options for Maven POM scanning."""

    ignore_dirs: Optional[list[str]] = None


@dataclass
class MavenParserConfig:
    """Parser options for Maven POM parsing."""

    include_test_scope: bool = False
    record_system_path: bool = True


@dataclass
class MavenCodeParserConfig:
    """Config for Maven Java code parser."""

    enable_native_detection: bool = True


@dataclass
class MavenLinkerConfig:
    """Config for Maven linker behaviour."""

    attach_test_sources: bool = False


@dataclass
class MavenEcosystemConfig:
    """Aggregate configuration for Maven ecosystem."""

    detect: MavenDetectConfig
    parser: MavenParserConfig
    code_parser: MavenCodeParserConfig
    linker: MavenLinkerConfig

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MavenEcosystemConfig":
        """Build MavenEcosystemConfig from mapping."""
        detect_data = data.get("detect", {}) or {}
        parser_data = data.get("parser", {}) or {}
        code_data = data.get("code_parser", {}) or {}
        linker_data = data.get("linker", {}) or {}

        detect_cfg = MavenDetectConfig(
            ignore_dirs=detect_data.get("ignore_dirs"),
        )

        parser_cfg = MavenParserConfig(
            include_test_scope=bool(
                parser_data.get("include_test_scope", False)
            ),
            record_system_path=bool(
                parser_data.get("record_system_path", True)
            ),
        )

        code_cfg = MavenCodeParserConfig(
            enable_native_detection=bool(
                code_data.get("enable_native_detection", True)
            )
        )

        linker_cfg = MavenLinkerConfig(
            attach_test_sources=bool(
                linker_data.get("attach_test_sources", False)
            )
        )

        return cls(
            detect=detect_cfg,
            parser=parser_cfg,
            code_parser=code_cfg,
            linker=linker_cfg,
        )


__all__ = [
    "MavenDetectConfig",
    "MavenParserConfig",
    "MavenCodeParserConfig",
    "MavenLinkerConfig",
    "MavenEcosystemConfig",
]
