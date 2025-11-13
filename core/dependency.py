"""Dependency specification types and models.

Core types for dependency management across the analysis pipeline.
Used by parsers to specify discovered dependencies and by fetchers to resolve them.
"""

import hashlib
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional


class DependencyType(Enum):
    """Supported ecosystem types for dependencies.

    Each type corresponds to a specific package manager or source system.
    """

    NPM = auto()
    MAVEN = auto()
    PIP = auto()
    GIT = auto()
    URL = auto()
    HVIGOR = auto()  # Hvigor/Ohpm packages


@dataclass
class DependencySpec:
    """Specification describing a third-party dependency.

    This is returned by parsers when they discover dependency declarations
    in configuration files (e.g., oh-package.json5, package.json).

    Attributes:
        name: Dependency package name.
        version: Version string (may be semver range).
        dependency_type: Package ecosystem type.
        source_url: Optional explicit source URL.
        parser_name: Name of parser that discovered this dependency.
        depth: Dependency depth (0 = direct, 1 = transitive, etc.).
    """

    name: str
    version: str
    dependency_type: DependencyType
    source_url: Optional[str] = None
    parser_name: str = ""
    depth: int = 0  # 0 means direct dependency

    @property
    def unique_key(self) -> str:
        """Generate unique key for deduplication.

        Returns:
            str: Unique key combining type, name, and version.
        """
        return f"{self.dependency_type.name}:{self.name}:{self.version}"

    @property
    def cache_path(self) -> str:
        """Generate relative cache path for this dependency.

        Returns:
            str: Relative path for caching dependency content.
        """
        key_hash = hashlib.md5(self.unique_key.encode()).hexdigest()[:12]
        return f"deps_cache/{self.dependency_type.name.lower()}/{key_hash}_{self.name}"


@dataclass
class DependencyResult:
    """Result of fetching a dependency.

    Returned by fetchers after attempting to download and cache a dependency.

    Attributes:
        spec: Original dependency specification.
        success: Whether fetch succeeded.
        local_path: Path to cached dependency (None if license_only or failed).
        error: Exception if fetch failed.
        discovered_deps: Transitive dependencies discovered during fetch.
        metadata: Additional metadata (e.g., license, registry info).
        license_only: True if only license info available (no source code).
    """

    spec: DependencySpec
    success: bool
    local_path: Optional[Path] = None
    error: Optional[Exception] = None
    discovered_deps: List[DependencySpec] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    license_only: bool = False
