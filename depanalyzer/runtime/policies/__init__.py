"""Policy interfaces and defaults for runtime lifecycle customization."""

from depanalyzer.runtime.policies.defaults import (
    DefaultAssetProjectionPolicy,
    DefaultAssetProjectionStrategy,
    DefaultCodeDependencyMapper,
)
from depanalyzer.runtime.policies.file_completeness import (
    FileCompletenessJoinPolicy,
    FallbackJoinStrategy,
)
from depanalyzer.runtime.policies.license_attach import LicenseAttachmentPolicy
from depanalyzer.runtime.policies.protocols import (
    AnalyzePolicy,
    AnalyzeStrategy,
    AssetProjectionPolicy,
    AssetProjectionStrategy,
    CodeDependencyContext,
    CodeDependencyMapper,
    ExportPolicy,
    ExportStrategy,
    JoinPolicy,
    JoinStrategy,
    LifecycleHook,
    ProjectionContext,
)

__all__ = [
    "AnalyzePolicy",
    "AnalyzeStrategy",
    "AssetProjectionPolicy",
    "AssetProjectionStrategy",
    "CodeDependencyContext",
    "CodeDependencyMapper",
    "DefaultAssetProjectionPolicy",
    "DefaultAssetProjectionStrategy",
    "DefaultCodeDependencyMapper",
    "ExportPolicy",
    "ExportStrategy",
    "FileCompletenessJoinPolicy",
    "FallbackJoinStrategy",
    "LicenseAttachmentPolicy",
    "JoinPolicy",
    "JoinStrategy",
    "LifecycleHook",
    "ProjectionContext",
]
