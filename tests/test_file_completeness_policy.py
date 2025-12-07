"""FileCompletenessJoinPolicy flat tree tests."""

from __future__ import annotations

from pathlib import Path

from depanalyzer.runtime.graph_config import GraphBuildConfig
from depanalyzer.runtime.context import JoinContext
from depanalyzer.runtime.eventbus import EventBus
from depanalyzer.runtime.lifecycle import LifecyclePhase
from depanalyzer.runtime.policies import FileCompletenessJoinPolicy
from depanalyzer.runtime.workspace import Workspace
from depanalyzer.graph import GraphManager, NodeType
from depanalyzer.graph.contract_registry import ContractRegistry


def _make_join_context(tmp_path: Path, graph: GraphManager, cfg: GraphBuildConfig) -> JoinContext:
    ws = Workspace(str(tmp_path))
    ws._root_path = tmp_path  # avoid filesystem mutations in tests
    return JoinContext(
        transaction_id="t",
        graph_id="g",
        source=str(tmp_path),
        workspace=ws,
        graph=graph,
        graph_build_config=cfg,
        eventbus=EventBus(),
        contract_registry=ContractRegistry.get_instance(),
        progress_manager=None,
        enable_dependency_resolution=False,
        max_dependency_depth=0,
        max_dependencies=None,
        current_phase=LifecyclePhase.JOIN,
    )


def test_fallback_attaches_untracked_files(tmp_path: Path) -> None:
    """Fallback creates flat tree when parsing is unavailable."""
    tracked = tmp_path / "known.txt"
    tracked.write_text("known", encoding="utf-8")
    untracked = tmp_path / "untracked.txt"
    untracked.write_text("u", encoding="utf-8")

    graph = GraphManager(root_path=tmp_path)
    graph.add_node(
        "known",
        NodeType.CODE,
        parser_name="test",
        src_path=str(tracked),
    )

    cfg = GraphBuildConfig.default()
    cfg.fallback.enabled = True
    strategy = FileCompletenessJoinPolicy(cfg.fallback)
    ctx = _make_join_context(tmp_path, graph, cfg)

    strategy.join(ctx)

    assert graph.has_node(cfg.fallback.root_id)
    # known file already tracked; untracked should be added and connected
    assert any("untracked" in n for n, _ in graph.nodes())
    assert graph.backend.has_edge(cfg.fallback.root_id, graph.normalize_path(untracked))


def test_fallback_connects_isolated_nodes(tmp_path: Path) -> None:
    """Isolated nodes get wired to fallback root."""
    cfg = GraphBuildConfig.default()
    cfg.fallback.enabled = True
    graph = GraphManager(root_path=tmp_path)
    graph.add_node("lonely", NodeType.CODE, parser_name="test")

    strategy = FileCompletenessJoinPolicy(cfg.fallback)
    ctx = _make_join_context(tmp_path, graph, cfg)
    strategy.join(ctx)

    assert graph.backend.has_edge(cfg.fallback.root_id, "lonely")
