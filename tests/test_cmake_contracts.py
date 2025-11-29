"""Tests for CMake↔Hvigor contract handling."""

from __future__ import annotations

from pathlib import Path

from depanalyzer.graph import GraphManager
from depanalyzer.graph.contract import BuildInterfaceContract, ContractType
from depanalyzer.graph.contract_registry import ContractRegistry
from depanalyzer.parsers.cpp.cmake_graph_builder import CMakeGraphBuilder
from depanalyzer.runtime.eventbus import Event, EventBus, EventType
from depanalyzer.runtime.transaction import Transaction


def test_cmake_graph_builder_registers_provider_contract(tmp_path: Path) -> None:
    """CMakeGraphBuilder should publish provider-side contracts for shared libraries."""
    registry = ContractRegistry.get_instance()
    registry.reset()

    cmake_dir = tmp_path / "native"
    cmake_dir.mkdir()
    cmake_file = cmake_dir / "CMakeLists.txt"
    cmake_file.write_text("", encoding="utf-8")

    graph_manager = GraphManager(graph_id="g", root_path=tmp_path)
    builder = CMakeGraphBuilder(graph_manager, EventBus())

    event = Event(
        event_type=EventType.CMAKE_TARGET_CREATED,
        source="cmake",
        data={
            "target_id": "//native/CMakeLists.txt:mynative",
            "node_type": "shared_library",
            "target_name": "mynative",
            "src_path": str(cmake_file),
            "declared_via": "add_library",
            "origin": "in_repo",
            "provenance": "cmake_add_target",
            "confidence": 1.0,
            "imported": False,
        },
    )

    builder._handle_target_created(event)

    providers = registry.get_providers("libmynative.so")
    registry.reset()

    assert providers, "Expected provider contract for shared_library target"
    provider = providers[0]
    assert provider.provider_artifact.startswith("//native")
    assert provider.metadata.get("target_id") == "//native/CMakeLists.txt:mynative"
    assert provider.metadata.get("native_dir") == str(cmake_dir.resolve())


def test_cmake_graph_builder_registers_provider_contract_for_static_library(tmp_path: Path) -> None:
    """Static libraries should also publish provider contracts (bridged as .so)."""
    registry = ContractRegistry.get_instance()
    registry.reset()

    cmake_dir = tmp_path / "native"
    cmake_dir.mkdir()
    cmake_file = cmake_dir / "CMakeLists.txt"
    cmake_file.write_text("", encoding="utf-8")

    graph_manager = GraphManager(graph_id="g", root_path=tmp_path)
    builder = CMakeGraphBuilder(graph_manager, EventBus())

    event = Event(
        event_type=EventType.CMAKE_TARGET_CREATED,
        source="cmake",
        data={
            "target_id": "//native/CMakeLists.txt:staticlib",
            "node_type": "static_library",
            "target_name": "staticlib",
            "src_path": str(cmake_file),
            "declared_via": "add_library",
            "origin": "in_repo",
            "provenance": "cmake_add_target",
            "confidence": 1.0,
            "imported": False,
        },
    )

    builder._handle_target_created(event)

    providers = registry.get_providers("libstaticlib.so")
    registry.reset()

    assert providers, "Expected provider contract for static_library target"
    provider = providers[0]
    assert provider.metadata.get("target_id") == "//native/CMakeLists.txt:staticlib"
    assert provider.metadata.get("native_dir") == str(cmake_dir.resolve())


def test_transaction_resets_contract_registry(tmp_path: Path) -> None:
    """Each transaction should start with a clean contract registry."""
    registry = ContractRegistry.get_instance()
    registry.reset()
    registry.register(
        BuildInterfaceContract(
            provider_artifact="//external/libfoo.so",
            consumer_artifact="",
            artifact_name="libfoo.so",
            contract_type=ContractType.ARTIFACT_NAME,
            confidence=0.5,
        )
    )
    assert registry.get_statistics()["total"] == 1

    project_root = tmp_path / "project"
    project_root.mkdir()

    tx = Transaction(str(project_root), max_workers=1)
    tx.run()

    stats = registry.get_statistics()
    registry.reset()
    assert stats["total"] == 0
