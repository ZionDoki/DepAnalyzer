"""Configuration schema definitions using Pydantic for validation.

This module provides strongly-typed configuration classes for all parsers
and components in depanalyzer. Using Pydantic ensures configuration errors
are caught early with clear error messages.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class ParserConfig(BaseModel):
    """Base configuration for all parsers.

    Attributes:
        include_test_scope: Whether to include test dependencies.
        max_depth: Maximum recursion depth for nested structures.
        enabled: Whether this parser is enabled.
    """

    include_test_scope: bool = False
    max_depth: int = Field(default=10, ge=1, le=100)
    enabled: bool = True

    model_config = {"extra": "allow"}  # Allow extra fields for extensibility


class DetectorConfig(BaseModel):
    """Configuration for target detectors.

    Attributes:
        enabled: Whether detection is enabled.
        ignore_patterns: Glob patterns to ignore during detection.
        max_targets: Maximum number of targets to detect (0 = unlimited).
    """

    enabled: bool = True
    ignore_patterns: List[str] = Field(default_factory=list)
    max_targets: int = Field(default=0, ge=0)

    model_config = {"extra": "allow"}


class LinkerConfig(BaseModel):
    """Configuration for linkers.

    Attributes:
        enabled: Whether linking is enabled.
        strict_mode: Whether to fail on unresolved references.
    """

    enabled: bool = True
    strict_mode: bool = False

    model_config = {"extra": "allow"}


class CodeParserConfig(BaseModel):
    """Configuration for code parsers.

    Attributes:
        enabled: Whether code parsing is enabled.
        file_timeout: Timeout for parsing a single file (seconds).
        max_file_size: Maximum file size to parse (bytes, 0 = unlimited).
    """

    enabled: bool = True
    file_timeout: float = Field(default=60.0, ge=1.0, le=600.0)
    max_file_size: int = Field(default=0, ge=0)

    model_config = {"extra": "allow"}


class MavenParserConfig(ParserConfig):
    """Configuration for Maven parser.

    Attributes:
        source_roots: List of source root directories relative to module.
        dependency_scopes: Maven dependency scopes to include.
        resolve_parent_pom: Whether to resolve parent POM references.
        include_optional: Whether to include optional dependencies.
    """

    source_roots: List[str] = Field(
        default_factory=lambda: ["src/main/java", "src/test/java"]
    )
    dependency_scopes: List[str] = Field(
        default_factory=lambda: ["compile", "runtime", "provided"]
    )
    resolve_parent_pom: bool = True
    include_optional: bool = False

    @field_validator("dependency_scopes")
    @classmethod
    def validate_scopes(cls, v: List[str]) -> List[str]:
        """Validate that dependency scopes are valid Maven scopes."""
        valid_scopes = {"compile", "provided", "runtime", "test", "system", "import"}
        for scope in v:
            if scope not in valid_scopes:
                raise ValueError(
                    f"Invalid Maven scope '{scope}'. Valid scopes: {valid_scopes}"
                )
        return v


class HvigorParserConfig(ParserConfig):
    """Configuration for HVigor (HarmonyOS) parser.

    Attributes:
        abi_list: List of ABIs to consider for native libraries.
        parse_native: Whether to parse native code references.
        resolve_ohpm: Whether to resolve OHPM package references.
    """

    abi_list: List[str] = Field(
        default_factory=lambda: ["arm64-v8a", "armeabi-v7a", "x86_64"]
    )
    parse_native: bool = True
    resolve_ohpm: bool = True

    @field_validator("abi_list")
    @classmethod
    def validate_abis(cls, v: List[str]) -> List[str]:
        """Validate that ABIs are non-empty strings."""
        if not v:
            raise ValueError("abi_list must contain at least one ABI")
        for abi in v:
            if not abi or not isinstance(abi, str):
                raise ValueError(f"Invalid ABI: {abi}")
        return v


class CMakeParserConfig(ParserConfig):
    """Configuration for CMake parser.

    Attributes:
        detect_cycles: Whether to detect and report circular dependencies.
        resolve_find_package: Whether to resolve find_package() calls.
        include_system_headers: Whether to include system header dependencies.
        variable_overrides: CMake variable overrides (name -> value).
    """

    detect_cycles: bool = True
    resolve_find_package: bool = True
    include_system_headers: bool = False
    variable_overrides: Dict[str, str] = Field(default_factory=dict)


class GraphBuildConfig(BaseModel):
    """Top-level configuration for graph building.

    This is the main configuration object passed to transactions.

    Attributes:
        maven: Maven parser configuration.
        hvigor: HVigor parser configuration.
        cmake: CMake parser configuration.
        detector: Detector configuration.
        linker: Linker configuration.
        code_parser: Code parser configuration.
        git_clone_timeout: Timeout for git clone operations (seconds).
        max_workers: Maximum number of worker threads.
        enable_caching: Whether to enable result caching.
    """

    maven: MavenParserConfig = Field(default_factory=MavenParserConfig)
    hvigor: HvigorParserConfig = Field(default_factory=HvigorParserConfig)
    cmake: CMakeParserConfig = Field(default_factory=CMakeParserConfig)
    detector: DetectorConfig = Field(default_factory=DetectorConfig)
    linker: LinkerConfig = Field(default_factory=LinkerConfig)
    code_parser: CodeParserConfig = Field(default_factory=CodeParserConfig)
    git_clone_timeout: int = Field(default=300, ge=60, le=3600)
    max_workers: int = Field(default=8, ge=1, le=64)
    enable_caching: bool = True

    def get_parser_config(self, ecosystem: str) -> Optional[ParserConfig]:
        """Get parser configuration for a specific ecosystem.

        Args:
            ecosystem: Ecosystem identifier (maven, hvigor, cmake, cpp).

        Returns:
            ParserConfig for the ecosystem, or None if not found.
        """
        ecosystem_map = {
            "maven": self.maven,
            "hvigor": self.hvigor,
            "cmake": self.cmake,
            "cpp": self.cmake,  # cpp uses cmake config
        }
        return ecosystem_map.get(ecosystem)

    def get_code_parser_config(self, ecosystem: str) -> CodeParserConfig:
        """Get code parser configuration.

        Args:
            ecosystem: Ecosystem identifier (currently unused, returns global config).

        Returns:
            CodeParserConfig for code parsing.
        """
        return self.code_parser

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GraphBuildConfig":
        """Create configuration from dictionary.

        Args:
            data: Configuration dictionary.

        Returns:
            GraphBuildConfig instance.

        Raises:
            ValidationError: If configuration is invalid.
        """
        return cls.model_validate(data)

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary.

        Returns:
            Configuration as dictionary.
        """
        return self.model_dump()
