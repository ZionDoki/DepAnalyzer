"""Maven parser and detector regression tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from depanalyzer.parsers.maven.detector import MavenDetector
from depanalyzer.parsers.maven.config_parser import MavenParser
from depanalyzer.parsers.maven.code_parser import MavenCodeParser, _TREE_SITTER_AVAILABLE
from depanalyzer.graph import GraphManager, NodeType
from depanalyzer.parsers.maven.code_dependency_mapper import MavenCodeDependencyMapper
from depanalyzer.runtime.eventbus import EventBus


def test_maven_detector_skips_target(tmp_path: Path) -> None:
    """Detector should ignore build output directories."""
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    (target_dir / "pom.xml").write_text("<project/>", encoding="utf-8")

    detector = MavenDetector(tmp_path, EventBus())
    results = detector.detect()
    assert len(results) == 1
    assert results[0].parent == tmp_path


def test_maven_parser_creates_module_and_dependency(tmp_path: Path) -> None:
    """POM parsing should create module, artifact, and dependency nodes."""
    pom = tmp_path / "pom.xml"
    pom.write_text(
        """
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>demo</artifactId>
  <version>1.0.0</version>
  <packaging>jar</packaging>
  <modules>
    <module>submod</module>
  </modules>
  <dependencies>
    <dependency>
      <groupId>org.sample</groupId>
      <artifactId>lib</artifactId>
      <version>0.1.0</version>
    </dependency>
  </dependencies>
</project>
""",
        encoding="utf-8",
    )

    eventbus = EventBus()
    graph = GraphManager(root_path=tmp_path)
    parser = MavenParser(tmp_path, graph, eventbus)
    parser.parse(pom)

    module_id = "module:maven:com.example:demo"
    dep_id = "ext_lib:maven:org.sample:lib@0.1.0"
    artifact_id = "artifact:maven:com.example:demo:jar"

    assert graph.has_node(module_id)
    assert graph.has_node(dep_id)
    assert graph.has_node(artifact_id)
    edge = graph.backend.get_edge_data(module_id, dep_id)
    assert edge is not None


def test_maven_code_parser_imports_and_jni(tmp_path: Path) -> None:
    """Java code parser should surface imports and native library hints."""
    if not _TREE_SITTER_AVAILABLE:  # pragma: no cover
        pytest.skip("tree-sitter-java not available in test environment")
    src_root = tmp_path / "src" / "main" / "java" / "com" / "example"
    src_root.mkdir(parents=True)
    java_file = src_root / "App.java"
    java_file.write_text(
        """
package com.example;

import java.util.List;
import com.other.Lib;

public class App {
  static { System.loadLibrary("nativeDemo"); }
}
""",
        encoding="utf-8",
    )

    parser = MavenCodeParser()
    result = parser.parse_file(java_file)

    assert "com.other.Lib" in result.get("imports", [])
    assert "nativeDemo" in result.get("native_libs", [])

    # Mapper should create import edge using module source roots.
    graph = GraphManager(root_path=tmp_path)
    graph.add_node(
        "module:maven:com.example:demo",
        NodeType.MODULE,
        parser_name="maven",
        src_path=str(tmp_path),
        source_roots=[str(src_root.parent.parent / "java")],
    )
    mapper = MavenCodeDependencyMapper()
    from depanalyzer.runtime.policies import CodeDependencyContext
    from depanalyzer.runtime.context import TransactionContext
    from depanalyzer.runtime.lifecycle import LifecyclePhase
    from depanalyzer.runtime.graph_config import GraphBuildConfig
    from depanalyzer.runtime.eventbus import EventBus
    from depanalyzer.runtime.workspace import Workspace
    from depanalyzer.graph.contract_registry import ContractRegistry

    workspace = Workspace(str(tmp_path))
    workspace._root_path = tmp_path  # Avoid filesystem mutation in tests

    tx_ctx = TransactionContext(
        transaction_id="t",
        graph_id=None,
        source=str(tmp_path),
        workspace=workspace,
        graph=graph,
        graph_build_config=GraphBuildConfig.default(),
        eventbus=EventBus(),
        contract_registry=ContractRegistry.get_instance(),
        display_manager=None,
        enable_dependency_resolution=False,
        max_dependency_depth=0,
        max_dependencies=None,
        current_phase=LifecyclePhase.PARSE,
    )
    mapper.map(CodeDependencyContext(tx_ctx, java_file, result))
    assert graph.edge_count() >= 1
