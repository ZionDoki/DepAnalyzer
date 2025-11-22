"""Tests covering the public runtime API helpers."""

from __future__ import annotations

from pathlib import Path

from depanalyzer.runtime.api import create_transaction
from depanalyzer.runtime.graph_config import GraphBuildConfig
from depanalyzer.runtime.lifecycle import LifecyclePhase


class DummyHook:
    """Minimal lifecycle hook used for testing."""

    phase = LifecyclePhase.DETECT

    def before(self, ctx) -> None:  # noqa: D401 - test stub
        return None

    def after(self, ctx) -> None:  # noqa: D401 - test stub
        return None


class DummyMapper:
    """Minimal code dependency mapper."""

    def map(self, ctx) -> None:  # noqa: D401 - test stub
        return None


class DummyProjectionPolicy:
    """Stub asset projection policy."""

    def project(self, ctx) -> None:  # noqa: D401 - test stub
        return None


class DummyJoinPolicy:
    """Stub join policy."""

    def join(self, ctx) -> None:  # noqa: D401 - test stub
        return None


class DummyAnalyzePolicy:
    """Stub analyze policy."""

    def analyze(self, ctx) -> None:  # noqa: D401 - test stub
        return None


def test_create_transaction_uses_default_configuration(tmp_path: Path) -> None:
    """create_transaction should populate default config and mappers."""

    src_dir = tmp_path / "src"
    src_dir.mkdir()

    tx = create_transaction(str(src_dir))

    state = tx._state  # Access for verification in tests
    assert state.graph_build_config == GraphBuildConfig.default()
    assert "cpp" in state.code_dependency_mappers
    assert "hvigor" in state.code_dependency_mappers
    assert state.lifecycle_hooks == []
    assert tx.graph_id is None


def test_create_transaction_accepts_custom_components(tmp_path: Path) -> None:
    """Custom hooks, mappers, and strategies should be preserved."""

    src_dir = tmp_path / "project"
    src_dir.mkdir()

    custom_config = GraphBuildConfig.default()
    hook = DummyHook()
    mapper = DummyMapper()
    projection = DummyProjectionPolicy()
    join_policy = DummyJoinPolicy()
    analyze_policy = DummyAnalyzePolicy()

    tx = create_transaction(
        str(src_dir),
        graph_build_config=custom_config,
        lifecycle_hooks=[hook],
        code_dependency_mappers={"custom": mapper},
        asset_projection_policy=projection,
        join_policies=[join_policy],
        analyze_policies=[analyze_policy],
        graph_id="graph-x",
        max_workers=2,
    )

    state = tx._state
    assert state.graph_build_config is custom_config
    assert state.lifecycle_hooks == [hook]
    assert state.code_dependency_mappers["custom"] is mapper
    assert state.asset_projection_policy is projection
    assert state.asset_projection_strategy is projection
    assert state.join_policies == [join_policy]
    assert state.join_strategies == [join_policy]
    assert state.analyze_policies == [analyze_policy]
    assert state.analyze_strategies == [analyze_policy]
    assert state.max_workers == 2
    assert tx.graph_id == "graph-x"
