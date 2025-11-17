"""Configuration models for graph projection.

This module contains small data classes that control how the
`GraphManager.derive_asset_artifact_projection` method derives
asset→artifact relationships from the raw graph.

The configuration is intentionally light‑weight and independent of the
runtime/transaction layer so it can be reused in different entry
points (CLI, tests, programmatic APIs).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ProjectionConfig:
    """Configuration for asset→artifact projection.

    Attributes:
        enable_fallback: Enable conservative fallbacks for unmatched assets.
        enable_link_closure: Propagate influence across link_libraries edges.
        enable_header_closure: Propagate include effects using multi-hop closure.
        max_header_hops: Max include hops for header closure.
        ignore_external_placeholders_in_fallback: Skip external placeholders in
            import/include-based fallbacks.
        nearest_dir_min_evidence: Minimal source evidence to accept
            nearest-directory fallback.
        link_closure_max_hops: Max hops across artifact links during link
            closure.
        resolve_include_placeholders: Resolve //include:*.h placeholders to
            concrete headers when possible.
        fuse_evidence: Fuse multi-evidence projection edges for compactness.
        fallback_max_targets_per_node: Optional cap for number of fallback
            targets attached per asset when inheriting from importers.
        fallback_disable_import_inherited: Disable import/include inheritance
            fallback when True.
        fallback_disable_nearest_dir: Disable nearest-directory fallback
            when True.
    """

    enable_fallback: bool = True
    enable_link_closure: bool = False
    enable_header_closure: bool = False
    max_header_hops: int = 2
    ignore_external_placeholders_in_fallback: bool = False
    nearest_dir_min_evidence: int = 0
    link_closure_max_hops: int = 3
    resolve_include_placeholders: bool = False
    fuse_evidence: bool = False
    fallback_max_targets_per_node: Optional[int] = None
    fallback_disable_import_inherited: bool = False
    fallback_disable_nearest_dir: bool = False


__all__ = ["ProjectionConfig"]

