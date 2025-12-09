"""Base detector, parser, and dependency fetcher interfaces for the new architecture.

Following AGENTS.md design: each ecosystem must implement three interfaces:
1. Detector - Lightweight scanning to identify parsing targets
2. Parser - Detailed parsing and graph construction
3. DepFetcher - Dependency fetching logic specific to the ecosystem
"""

# Parsers catch broad exceptions to keep pipelines moving when repos have malformed inputs.


import logging
import shutil
import subprocess
import tarfile
import urllib.error
import zipfile

import requests
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from depanalyzer.graph import GraphManager
from depanalyzer.runtime.context import TransactionContext
from depanalyzer.runtime.eventbus import Event, EventBus
from depanalyzer.runtime.policies import CodeDependencyContext, CodeDependencyMapper

if TYPE_CHECKING:
    from depanalyzer.graph.contract_registry import ContractRegistry

logger = logging.getLogger("depanalyzer.parsers.base")


# =============================================================================
# Custom Exception Hierarchy
# =============================================================================

class RecoverableError(Exception):
    """Base class for recoverable business errors.

    These errors indicate expected failure conditions that can be handled
    gracefully by skipping the current item and continuing processing.
    """
    pass


class ConfigurationError(RecoverableError):
    """Configuration file error - can skip current target and continue.

    Raised when a configuration file (e.g., pom.xml, CMakeLists.txt) is
    malformed or contains invalid data.
    """
    pass


class ParseError(RecoverableError):
    """Source code parsing error - can skip current file and continue.

    Raised when a source code file cannot be parsed due to syntax errors
    or unsupported constructs.
    """
    pass


class FetchError(RecoverableError):
    """Dependency fetching error - can skip current dependency and continue.

    Raised when a dependency cannot be downloaded or cloned.
    """
    pass


class DetectionError(RecoverableError):
    """Target detection error - can skip current directory and continue.

    Raised when target detection fails for a specific location.
    """
    pass


# =============================================================================
# Exception Categories for Graceful Handling
# =============================================================================

# External system errors (network, subprocess, archive) - recoverable
_EXTERNAL_ERRORS = (
    subprocess.CalledProcessError,
    subprocess.TimeoutExpired,
    urllib.error.URLError,
    urllib.error.HTTPError,
    zipfile.BadZipFile,
    tarfile.TarError,
)

# Data/lookup errors - recoverable when caused by malformed input
_DATA_ERRORS = (
    KeyError,
    IndexError,
)

# I/O errors - recoverable (file not found, permission denied, etc.)
_IO_ERRORS = (
    OSError,
    IOError,
)

# Combined safe exceptions for backward compatibility
# NOTE: Removed TypeError, AttributeError, RuntimeError, ImportError as these
# typically indicate programming errors that should propagate.
_SAFE_EXCEPTIONS = (
    _EXTERNAL_ERRORS
    + _DATA_ERRORS
    + _IO_ERRORS
    + (RecoverableError, ValueError, UnicodeDecodeError)
)


class BaseDetector(ABC):
    """Base class for target detectors.

    Detectors perform lightweight scanning to identify parsing targets
    (e.g., CMakeLists.txt, hvigorfile.ts) and publish detection events.
    """

    NAME: str = "base"
    ECOSYSTEM: str = "base"  # Ecosystem identifier (cpp, hvigor, npm, etc.)

    def __init__(
        self,
        workspace_root: Path,
        eventbus: EventBus,
        config: Optional[Any] = None,
    ) -> None:
        """Initialize detector.

        Args:
            workspace_root: Workspace root path.
            eventbus: Event bus for publishing detection events.
        """
        self.workspace_root = workspace_root
        self.eventbus = eventbus
        # Optional per‑ecosystem configuration slice, provided by the
        # transaction's GraphBuildConfig.detect.for_ecosystem().
        self.config = config
        logger.debug("Detector %s (%s) initialized", self.NAME, self.ECOSYSTEM)

    @abstractmethod
    def detect(self) -> List[Path]:
        """Detect parsing targets in workspace.

        Returns:
            List[Path]: List of detected target paths.
        """
        raise NotImplementedError

    def scan_workspace(
        self,
        patterns: List[str],
        ignore_patterns: Optional[List[str]] = None,
        recursive: bool = True,
    ) -> List[Path]:
        """Scan workspace for files matching patterns.

        This helper uses the optimized scanner that respects .gitignore.

        Args:
            patterns: List of glob patterns to match.
            ignore_patterns: Optional list of extra ignore patterns.
            recursive: Whether to scan recursively.

        Returns:
            List[Path]: List of matching file paths.
        """
        from depanalyzer.utils.scanner import load_gitignore_patterns, scan_files

        root_ignores = load_gitignore_patterns(self.workspace_root)
        if ignore_patterns:
            root_ignores.extend(ignore_patterns)

        return list(
            scan_files(
                self.workspace_root,
                patterns,
                ignore_patterns=root_ignores,
                recursive=recursive,
            )
        )

    def publish_detection_event(self, event: Event) -> None:
        """Publish detection event to event bus.

        Args:
            event: Detection event.
        """
        self.eventbus.publish(event)


class BaseParser(ABC):
    """Base class for parsers.

    Parsers consume detection events and perform detailed parsing,
    publishing structured events for hook consumption.
    """

    NAME: str = "base"
    ECOSYSTEM: str = "base"  # Ecosystem identifier

    def __init__(
        self,
        workspace_root: Path,
        graph_manager: GraphManager,
        eventbus: EventBus,
        config: Optional[Any] = None,
        contract_registry: Optional["ContractRegistry"] = None,
    ) -> None:
        """Initialize parser.

        Args:
            workspace_root: Workspace root path.
            graph_manager: Transaction graph manager.
            eventbus: Event bus for publishing parse events.
            config: Optional parser configuration.
            contract_registry: Contract registry for cross-language linking.
        """
        self.workspace_root = workspace_root
        self.graph_manager = graph_manager
        self.eventbus = eventbus
        # Optional per‑ecosystem configuration slice, provided by the
        # transaction's GraphBuildConfig.parser.for_ecosystem().
        self.config = config
        # Use provided registry or fallback to legacy global singleton
        if contract_registry:
            self.contract_registry = contract_registry
        else:
            from depanalyzer.graph.contract_registry import ContractRegistry
            self.contract_registry = ContractRegistry.get_instance()
            
        logger.debug("Parser %s (%s) initialized", self.NAME, self.ECOSYSTEM)

    @abstractmethod
    def parse(self, target_path: Path) -> None:
        """Parse a detected target.

        Args:
            target_path: Path to parsing target.
        """
        raise NotImplementedError

    def publish_parse_event(self, event: Event) -> None:
        """Publish parse event to event bus.

        Args:
            event: Parse event.
        """
        self.eventbus.publish(event)

    def add_node(self, node_id: str, node_type: str, **attributes) -> None:
        """Helper to add node to transaction graph.

        Args:
            node_id: Node identifier.
            node_type: Node type.
            **attributes: Additional attributes.
        """
        self.graph_manager.add_node(node_id, node_type, **attributes)

    def add_edge(self, source: str, target: str, edge_kind: str, **attributes) -> None:
        """Helper to add edge to transaction graph.

        Args:
            source: Source node ID.
            target: Target node ID.
            edge_kind: Edge kind.
            **attributes: Additional attributes.
        """
        self.graph_manager.add_edge(source, target, edge_kind, **attributes)

    def discover_code_files(self) -> List[Path]:
        """Discover code files that need to be parsed.

        Config parsers can override this to discover source files based on
        configuration content (e.g., from CMakeLists.txt, package.json).

        Returns:
            List[Path]: List of source code files to parse. Empty list means no code parsing needed.
        """
        return []


class BaseLinker(ABC):
    """Base class for per-ecosystem linkers.

    Linkers operate on the already-populated transaction graph and
    derive configuration- or ecosystem-driven relationships, such as:
    - module→code membership
    - module/shared_library→native target wiring
    - cross-ecosystem bridges based on build contracts

    They are invoked after all parsers have run, typically during the
    JOIN phase of a transaction.
    """

    ECOSYSTEM: str = "base"

    def __init__(
        self,
        graph_manager: GraphManager,
        config: Optional[Any] = None,
        contract_registry: Optional["ContractRegistry"] = None,
    ) -> None:
        """Initialize linker with graph manager.

        Args:
            graph_manager: Transaction graph manager.
            config: Optional linker configuration.
            contract_registry: Contract registry for cross-language linking.
        """
        self.graph_manager = graph_manager
        # Optional per‑ecosystem configuration slice for linkers.
        self.config = config
        # Use provided registry or fallback to legacy global singleton
        if contract_registry:
            self.contract_registry = contract_registry
        else:
            from depanalyzer.graph.contract_registry import ContractRegistry
            self.contract_registry = ContractRegistry.get_instance()

    @abstractmethod
    def link(self) -> None:
        """Apply linking logic to the current graph.

        Implementations should be idempotent and robust to partial
        graphs, as they may be executed in different analysis setups.
        """
        raise NotImplementedError


class BaseCodeParser(ABC):
    """Base class for code file parsers.

    Code parsers extract fine-grained dependencies from source code files
    (e.g., #include in C++, import in TypeScript). They are executed in
    a global process pool for maximum parallelism.

    Important: parse_file() must be a pure function (stateless) that can
    safely execute in worker processes.
    """

    NAME: str = "base_code"
    ECOSYSTEM: str = "base"
    CODE_GLOBS: List[str] = []  # e.g., ["**/*.c", "**/*.cpp"]

    @abstractmethod
    def parse_file(self, file_path: Path) -> Dict[str, Any]:
        """Parse a single source code file.

        This method MUST be a pure function (no side effects) that can
        safely execute in worker processes. Do not access instance state
        that requires synchronization.

        Args:
            file_path: Path to source code file.

        Returns:
            Dict[str, Any]: Parse result with structure:
                {
                    "file": str,
                    "includes": List[Path],  # or "imports"
                    "exports": Optional[List[str]],
                    ...
                }
        """
        raise NotImplementedError

    def can_handle_file(self, file_path: Path) -> bool:
        """Check if this parser can handle the given file.

        Args:
            file_path: Path to file to check.

        Returns:
            bool: True if file extension matches CODE_GLOBS.
        """
        return any(file_path.match(glob) for glob in self.CODE_GLOBS)


class BaseCodeDependencyMapper(CodeDependencyMapper, ABC):
    """Base class for per-ecosystem code dependency mappers.

    This base class adapts the ``CodeDependencyMapper`` protocol to the
    ecosystem-oriented patterns used under ``parsers/``. Implementations
    declare their ``ECOSYSTEM`` and only receive callbacks for matching
    parse results.

    Subclasses should implement :meth:`_map_for_file` to perform the
    actual graph updates.
    """

    NAME: str = "base_code_mapper"
    ECOSYSTEM: str = "base"

    def map(self, ctx: CodeDependencyContext) -> None:
        """Dispatch mapping for a single parsed source file.

        This method performs common precondition checks and ensures that
        the mapper runs only for the configured ecosystem. Subclasses
        should not override this method; instead, implement
        :meth:`_map_for_file`.
        """
        parse_result = ctx.parse_result
        ecosystem = parse_result.get("ecosystem")

        # If the parse result is ecosystem-tagged and does not match
        # this mapper's ecosystem, skip without error.
        if ecosystem and ecosystem != self.ECOSYSTEM:
            return

        tx: TransactionContext = ctx.transaction_ctx
        graph = tx.graph
        if graph is None:
            return

        self._map_for_file(
            transaction_ctx=tx,
            graph=graph,
            file_path=ctx.file_path,
            parse_result=parse_result,
        )

    @abstractmethod
    def _map_for_file(
        self,
        transaction_ctx: TransactionContext,
        graph: GraphManager,
        file_path: Path,
        parse_result: Dict[str, Any],
    ) -> None:
        """Perform ecosystem-specific mapping for a parsed source file.

        Args:
            transaction_ctx: Transaction context snapshot.
            graph: GraphManager instance for the current transaction.
            file_path: Path to the parsed source file.
            parse_result: Parse result dictionary.
        """
        raise NotImplementedError


class DependencySpec:
    """Specification for a dependency to be fetched.

    This is a simplified version that each ecosystem's DepFetcher will handle.
    """

    def __init__(
        self,
        name: str,
        version: Optional[str] = None,
        source_url: Optional[str] = None,
        ecosystem: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> None:
        """Initialize dependency specification.

        Args:
            name: Dependency name.
            version: Version specification (semver, git tag, etc.).
            source_url: Source URL (git URL, registry URL, etc.).
            ecosystem: Target ecosystem identifier.
            metadata: Additional metadata specific to the ecosystem.
        """
        self.name = name
        self.version = version
        self.source_url = source_url
        self.ecosystem = ecosystem
        self.metadata = metadata or {}

    def __repr__(self) -> str:
        return (
            f"DependencySpec(name={self.name}, "
            f"version={self.version}, ecosystem={self.ecosystem})"
        )

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, DependencySpec):
            return NotImplemented
        return (
            self.name == other.name
            and self.version == other.version
            and self.ecosystem == other.ecosystem
            and self.source_url == other.source_url
        )

    def __hash__(self) -> int:
        return hash((self.name, self.version, self.ecosystem, self.source_url))


class BaseDepFetcher(ABC):
    """Base class for dependency fetchers.

    Each ecosystem implements its own DepFetcher to handle dependency
    downloading/cloning logic specific to that ecosystem.

    Provides git clone as a common utility method that can be reused.
    """

    NAME: str = "base"
    ECOSYSTEM: str = "base"  # Ecosystem identifier

    def __init__(self, cache_root: Path) -> None:
        """Initialize dependency fetcher.

        Args:
            cache_root: Root directory for dependency cache.
        """
        self.cache_root = cache_root
        self.cache_root.mkdir(parents=True, exist_ok=True)
        logger.debug("DepFetcher %s (%s) initialized", self.NAME, self.ECOSYSTEM)

    @abstractmethod
    def fetch(self, dep_spec: DependencySpec) -> Optional[Path]:
        """Fetch a dependency to local cache.

        Args:
            dep_spec: Dependency specification.

        Returns:
            Optional[Path]: Local path to fetched dependency, None if failed.
        """
        raise NotImplementedError

    @abstractmethod
    def can_handle(self, dep_spec: DependencySpec) -> bool:
        """Check if this fetcher can handle the given dependency.

        Args:
            dep_spec: Dependency specification.

        Returns:
            bool: True if this fetcher can handle the dependency.
        """
        raise NotImplementedError

    def get_cache_dir(self, dep_spec: DependencySpec) -> Path:
        """Get cache directory for a specific dependency.

        Args:
            dep_spec: Dependency specification.

        Returns:
            Path: Cache directory path.
        """
        # Default: ecosystem/name/version
        parts = [self.ECOSYSTEM, dep_spec.name]
        if dep_spec.version:
            parts.append(dep_spec.version)
        return self.cache_root / Path(*parts)

    def git_clone(
        self,
        url: str,
        target_dir: Path,
        branch: Optional[str] = None,
        tag: Optional[str] = None,
        depth: int = 1,
        force: bool = False,
    ) -> bool:
        """Clone a git repository (common utility method).

        Args:
            url: Git repository URL.
            target_dir: Target directory for cloning.
            branch: Specific branch to clone (optional).
            tag: Specific tag to clone (optional).
            depth: Clone depth (default: 1 for shallow clone).
            force: Force re-clone if directory exists.

        Returns:
            bool: True if clone succeeded, False otherwise.
        """
        # Check if already exists
        if target_dir.exists():
            if force:
                logger.info(
                    "Force re-cloning, removing existing directory: %s", target_dir
                )
                shutil.rmtree(target_dir)
            else:
                logger.info("Repository already exists at: %s", target_dir)
                return True

        # Ensure parent directory exists
        target_dir.parent.mkdir(parents=True, exist_ok=True)

        # Build git clone command
        cmd = ["git", "clone", "--depth", str(depth)]

        if branch:
            cmd.extend(["--branch", branch])
        elif tag:
            cmd.extend(["--branch", tag])

        cmd.extend([url, str(target_dir)])

        logger.info("Cloning git repository: %s", url)
        logger.debug("Git clone command: %s", " ".join(cmd))

        try:
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
                check=True,
            )
            logger.info("Successfully cloned to: %s", target_dir)
            return True

        except subprocess.TimeoutExpired:
            logger.error("Git clone timed out after 5 minutes: %s", url)
            return False

        except subprocess.CalledProcessError as e:
            logger.error("Git clone failed: %s", e.stderr)
            return False

        except _SAFE_EXCEPTIONS as e:
            logger.error("Unexpected error during git clone: %s", e)
            return False

    def download_file(self, url: str, target_path: Path, timeout: int = 300) -> bool:
        """Download a file from URL (common utility method).

        Uses requests library with SSL verification enabled by default.

        Args:
            url: File URL.
            target_path: Target file path.
            timeout: Request timeout in seconds (default: 300).

        Returns:
            bool: True if download succeeded, False otherwise.
        """
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)

            logger.info("Downloading file: %s", url)
            response = requests.get(url, timeout=timeout, stream=True)
            response.raise_for_status()
            with target_path.open("wb") as out:
                for chunk in response.iter_content(chunk_size=8192):
                    out.write(chunk)
            logger.info("Downloaded to: %s", target_path)
            return True

        except requests.RequestException as e:
            logger.error("Failed to download file %s: %s", url, e)
            return False
        except _SAFE_EXCEPTIONS as e:
            logger.error("Failed to download file %s: %s", url, e)
            return False

    def extract_archive(self, archive_path: Path, target_dir: Path) -> bool:
        """Extract archive file (common utility method).

        Supports: .zip, .tar.gz, .tar.bz2, .tar.xz

        This method uses safe extraction to prevent path traversal attacks
        (Zip Slip, Tar Slip).

        Args:
            archive_path: Path to archive file.
            target_dir: Target directory for extraction.

        Returns:
            bool: True if extraction succeeded, False otherwise.
        """
        from depanalyzer.utils.archive_utils import (
            ArchiveSecurityError,
            safe_extract,
        )

        try:
            logger.info("Extracting archive: %s", archive_path)
            safe_extract(archive_path, target_dir)
            logger.info("Extracted to: %s", target_dir)
            return True

        except ArchiveSecurityError as e:
            logger.error("Security error extracting archive %s: %s", archive_path, e)
            return False
        except ValueError as e:
            logger.error("Unsupported archive format %s: %s", archive_path, e)
            return False
        except _SAFE_EXCEPTIONS as e:
            logger.error("Failed to extract archive %s: %s", archive_path, e)
            return False
