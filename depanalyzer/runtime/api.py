"""Library-facing helpers for creating configured transactions.

The CLI uses the ``Transaction`` class directly. This module provides a
slim convenience wrapper that allows library users to construct a
transaction with custom lifecycle hooks and policies in a single
call.
"""

from __future__ import annotations

from typing import Mapping, Optional, Sequence

from depanalyzer.runtime.graph_config import GraphBuildConfig
from depanalyzer.runtime.policies import (
    AnalyzePolicy,
    AssetProjectionPolicy,
    CodeDependencyMapper,
    JoinPolicy,
    LifecycleHook,
)
from depanalyzer.runtime.transaction import Transaction


def create_transaction(
    source: str,
    graph_build_config: Optional[GraphBuildConfig] = None,
    lifecycle_hooks: Optional[Sequence[LifecycleHook]] = None,
    code_dependency_mappers: Optional[Mapping[str, CodeDependencyMapper]] = None,
    asset_projection_strategy: Optional[AssetProjectionPolicy] = None,
    join_strategies: Optional[Sequence[JoinPolicy]] = None,
    analyze_strategies: Optional[Sequence[AnalyzePolicy]] = None,
    asset_projection_policy: Optional[AssetProjectionPolicy] = None,
    join_policies: Optional[Sequence[JoinPolicy]] = None,
    analyze_policies: Optional[Sequence[AnalyzePolicy]] = None,
    **kwargs: object,
) -> Transaction:
    """Create a Transaction with optional hooks and policies.

    Args:
        source: Source path or Git URL for the transaction.
        graph_build_config: Optional GraphBuildConfig instance. When
            omitted, ``GraphBuildConfig.default()`` is used.
        lifecycle_hooks: Optional sequence of lifecycle hooks.
        code_dependency_mappers: Optional mapping from ecosystem name
            to CodeDependencyMapper implementation.
        asset_projection_strategy: Optional AssetProjectionPolicy used during ANALYZE
            (deprecated alias).
        join_strategies: Optional sequence of JoinPolicy instances (deprecated alias).
        analyze_strategies: Optional sequence of AnalyzePolicy instances
            (deprecated alias).
        asset_projection_policy: Optional AssetProjectionPolicy used during ANALYZE.
        join_policies: Optional sequence of JoinPolicy instances.
        analyze_policies: Optional sequence of AnalyzePolicy instances.
        **kwargs: Additional keyword arguments passed through to
            ``Transaction.__init__``.

    Returns:
        Configured Transaction instance.
    """
    asset_policy = asset_projection_policy or asset_projection_strategy
    join_policy_seq = join_policies or join_strategies
    analyze_policy_seq = analyze_policies or analyze_strategies

    return Transaction(
        source=source,
        graph_build_config=graph_build_config or GraphBuildConfig.default(),
        lifecycle_hooks=lifecycle_hooks,
        code_dependency_mappers=code_dependency_mappers,
        asset_projection_policy=asset_policy,
        join_policies=join_policy_seq,
        analyze_policies=analyze_policy_seq,
        **kwargs,
    )


__all__ = ["create_transaction"]
