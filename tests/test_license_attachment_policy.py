"""LicenseAttachmentPolicy tests."""

from __future__ import annotations

from pathlib import Path

from depanalyzer.graph import EdgeKind, GraphManager, NodeType
from depanalyzer.graph.contract_registry import ContractRegistry
from depanalyzer.runtime.context import JoinContext
from depanalyzer.runtime.eventbus import EventBus
from depanalyzer.runtime.graph_config import GraphBuildConfig
from depanalyzer.runtime.lifecycle import LifecyclePhase
from depanalyzer.runtime.policies import LicenseAttachmentPolicy
from depanalyzer.runtime.workspace import Workspace


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
        display_manager=None,
        enable_dependency_resolution=False,
        max_dependency_depth=0,
        max_dependencies=None,
        current_phase=LifecyclePhase.JOIN,
    )


def test_license_policy_attaches_to_root_nodes(tmp_path: Path) -> None:
    """License files are attached to non-isolated in-degree-zero nodes."""
    license_file = tmp_path / "LICENSE"
    license_file.write_text("test", encoding="utf-8")

    graph = GraphManager(root_path=tmp_path)
    root_id = "hap:app"
    module_id = "module:foo"
    graph.add_node(root_id, NodeType.HAP, parser_name="test")
    graph.add_node(
        module_id,
        NodeType.MODULE,
        parser_name="test",
        src_path=str(tmp_path / "module"),
    )
    graph.add_edge(root_id, module_id, EdgeKind.CONTAINS.value, parser_name="test")
    isolated_id = "lonely"
    graph.add_node(isolated_id, NodeType.CODE, parser_name="test")

    cfg = GraphBuildConfig.default()
    policy = LicenseAttachmentPolicy(cfg.license_link)
    ctx = _make_join_context(tmp_path, graph, cfg)

    policy.join(ctx)

    license_id = graph.normalize_path(license_file)
    assert graph.get_node(license_id)["type"] == NodeType.LICENSE.value
    assert graph.backend.has_edge(root_id, license_id)
    assert not graph.backend.has_edge(isolated_id, license_id)


def test_license_policy_retypes_existing_node(tmp_path: Path) -> None:
    """Existing nodes representing license files are retyped."""
    license_file = tmp_path / "COPYING.txt"
    license_file.write_text("copy", encoding="utf-8")

    graph = GraphManager(root_path=tmp_path)
    root_id = "hsp:lib"
    graph.add_node(root_id, NodeType.HSP, parser_name="test")
    graph.add_node("child", NodeType.CODE, parser_name="test")
    graph.add_edge(root_id, "child", EdgeKind.CONTAINS.value, parser_name="test")

    node_id = graph.normalize_path(license_file)
    graph.add_node(node_id, NodeType.CODE, parser_name="fallback")

    cfg = GraphBuildConfig.default()
    policy = LicenseAttachmentPolicy(cfg.license_link)
    ctx = _make_join_context(tmp_path, graph, cfg)

    policy.join(ctx)

    attrs = graph.get_node(node_id)
    assert attrs["type"] == NodeType.LICENSE.value
    assert graph.backend.has_edge(root_id, node_id)
