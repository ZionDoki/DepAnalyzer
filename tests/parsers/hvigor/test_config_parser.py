from pathlib import Path

from depanalyzer.graph import EdgeKind, GraphManager, NodeType
from depanalyzer.parsers.hvigor.config_parser import HvigorParser
from depanalyzer.runtime.eventbus import EventBus


def test_root_level_dependencies_linked_to_all_modules(tmp_path: Path) -> None:
    build_profile = tmp_path / "build-profile.json5"
    build_profile.write_text('{"modules": ["entry", "core"]}', encoding="utf-8")

    package_cfg = tmp_path / "oh-package.json5"
    package_cfg.write_text(
        '{"dependencies": {"ohos_smart_dialog": "1.3.6"}}',
        encoding="utf-8",
    )

    gm = GraphManager(root_path=tmp_path)
    parser = HvigorParser(tmp_path, gm, EventBus())

    parser.parse(build_profile)
    parser.parse(package_cfg)

    expected_lib = "ext_lib:ohos_smart_dialog@1.3.6"
    graph = gm.backend.native_graph

    for module_name in ("entry", "core"):
        module_id = f"module:{module_name}"
        assert graph.has_edge(module_id, expected_lib)
        edge_data = graph.get_edge_data(module_id, expected_lib)
        assert any(
            (attrs or {}).get("kind") == EdgeKind.DEPENDS_ON.value
            for attrs in edge_data.values()
        )


def test_root_level_dependencies_apply_to_modules_discovered_late(tmp_path: Path) -> None:
    """Root-level oh-package deps should attach to modules parsed after the root file."""
    package_cfg = tmp_path / "oh-package.json5"
    package_cfg.write_text('{"dependencies": {"late_mod": "0.1.0"}}', encoding="utf-8")

    # Create two modules with module.json5 arriving after the root package file
    mod_a = tmp_path / "alpha" / "src" / "main"
    mod_a.mkdir(parents=True)
    mod_b = tmp_path / "zeta" / "src" / "main"
    mod_b.mkdir(parents=True)
    for mod_root, name in [(mod_a, "alpha"), (mod_b, "zeta")]:
        module_json = mod_root / "module.json5"
        module_json.write_text(
            f'{{"module": {{"name": "{name}", "type": "entry"}}}}',
            encoding="utf-8",
        )

    gm = GraphManager(root_path=tmp_path)
    parser = HvigorParser(tmp_path, gm, EventBus())

    # Parse root package first (will be deferred), then modules
    parser.parse(package_cfg)
    parser.parse(mod_a / "module.json5")
    parser.parse(mod_b / "module.json5")

    expected_lib = "ext_lib:late_mod@0.1.0"
    graph = gm.backend.native_graph
    for module_name in ("alpha", "zeta"):
        module_id = f"module:{module_name}"
        assert graph.has_edge(module_id, expected_lib)
        edge_data = graph.get_edge_data(module_id, expected_lib)
        assert any(
            (attrs or {}).get("kind") == EdgeKind.DEPENDS_ON.value
            for attrs in edge_data.values()
        )


def test_root_lock_applies_to_modules_discovered_late(tmp_path: Path) -> None:
    """Root-level lock file should apply to modules parsed after it."""
    lock_cfg = tmp_path / "oh-package-lock.json5"
    lock_cfg.write_text(
        '{"packages": {"node_modules/libfoo": {"name": "libfoo", "version": "1.2.3", "registryType": "ohpm"}}}',
        encoding="utf-8",
    )

    mod_dir = tmp_path / "only" / "src" / "main"
    mod_dir.mkdir(parents=True)
    module_json = mod_dir / "module.json5"
    module_json.write_text(
        '{"module": {"name": "only", "type": "entry"}}',
        encoding="utf-8",
    )

    gm = GraphManager(root_path=tmp_path)
    parser = HvigorParser(tmp_path, gm, EventBus())

    parser.parse(lock_cfg)
    parser.parse(module_json)

    lib_id = "ext_lib:libfoo@1.2.3"
    graph = gm.backend.native_graph
    assert graph.has_edge("module:only", lib_id)
    edge_data = graph.get_edge_data("module:only", lib_id)
    assert any(
        (attrs or {}).get("kind") == EdgeKind.DEPENDS_ON.value
        for attrs in edge_data.values()
    )


def test_build_profile_marks_native_modules_with_external_native_options(tmp_path: Path) -> None:
    """externalNativeOptions should mark modules as native-capable."""
    module_root = tmp_path / "entry"
    cmake_dir = module_root / "src" / "main" / "cpp"
    cmake_dir.mkdir(parents=True)
    (cmake_dir / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.10)\n", encoding="utf-8")

    build_profile = tmp_path / "build-profile.json5"
    build_profile.write_text(
        '{"modules": [{"name": "entry", "externalNativeOptions": {"path": "src/main/cpp/CMakeLists.txt"}}]}',
        encoding="utf-8",
    )

    gm = GraphManager(root_path=tmp_path)
    parser = HvigorParser(tmp_path, gm, EventBus())

    parser.parse(build_profile)

    node = gm.get_node("module:entry")
    assert node is not None
    assert node.get("has_native_code") is True
    native_dirs = node.get("native_dirs") or []
    assert str(cmake_dir.resolve()) in native_dirs
    evidence = node.get("native_evidence") or []
    assert "externalNativeOptions" in evidence


def test_module_config_detects_shared_library_artifacts(tmp_path: Path) -> None:
    """Module config should record on-disk shared libraries as native evidence."""
    module_root = tmp_path / "feature"
    lib_dir = module_root / "libs" / "arm64-v8a"
    lib_dir.mkdir(parents=True)
    so_file = lib_dir / "libdemo.so"
    so_file.write_bytes(b"\x7fELF")

    module_json = module_root / "src" / "main" / "module.json5"
    module_json.parent.mkdir(parents=True)
    module_json.write_text(
        '{"module": {"name": "feature", "type": "har"}}',
        encoding="utf-8",
    )

    gm = GraphManager(root_path=tmp_path)
    parser = HvigorParser(tmp_path, gm, EventBus())

    parser.parse(module_json)

    node = gm.get_node("module:feature")
    assert node is not None
    assert node.get("has_native_code") is True
    artifacts = node.get("native_artifacts") or []
    assert gm.normalize_path(so_file) in artifacts


def test_module_json_sets_module_type(tmp_path: Path) -> None:
    """Plain module.json files should still surface module type metadata."""
    module_json = tmp_path / "lib" / "src" / "main" / "module.json"
    module_json.parent.mkdir(parents=True)
    module_json.write_text(
        '{"module": {"name": "lib", "type": "har"}}',
        encoding="utf-8",
    )

    gm = GraphManager(root_path=tmp_path)
    parser = HvigorParser(tmp_path, gm, EventBus())

    parser.parse(module_json)

    node = gm.get_node("module:lib")
    assert node is not None
    assert node.get("type") == NodeType.HAR.value
    assert node.get("module_type") == "har"
    assert node.get("declared_via") == "module.json"


def test_root_package_infers_module_when_missing_build_profile(tmp_path: Path) -> None:
    """Root-level oh-package.json5 should still create a package-level module node."""
    package_cfg = tmp_path / "oh-package.json5"
    package_cfg.write_text(
        '{"name": "demo.app", "dependencies": {"hv-lib": "1.0.0"}}',
        encoding="utf-8",
    )

    gm = GraphManager(root_path=tmp_path)
    parser = HvigorParser(tmp_path, gm, EventBus())

    parser.parse(package_cfg)

    graph = gm.backend.native_graph
    module_ids = [
        nid for nid, attrs in gm.nodes() if attrs.get("type") == NodeType.MODULE.value
    ]
    assert module_ids
    module_id = module_ids[0]
    assert module_id == "module:demo.app"

    ext_lib_id = "ext_lib:hv-lib@1.0.0"
    assert graph.has_edge(module_id, ext_lib_id)
    # Ensure config file is not reused as a package surrogate.
    assert not graph.has_edge(str(package_cfg.relative_to(tmp_path)), ext_lib_id)


def test_inferred_root_package_not_replayed_on_flush(tmp_path: Path) -> None:
    """Fallback root module should not get duplicated edges when modules arrive later."""
    package_cfg = tmp_path / "oh-package.json5"
    package_cfg.write_text(
        '{"name": "demo.app", "dependencies": {"hv-lib": "1.0.0"}}',
        encoding="utf-8",
    )

    gm = GraphManager(root_path=tmp_path)
    parser = HvigorParser(tmp_path, gm, EventBus())

    parser.parse(package_cfg)  # creates inferred module:demo.app
    build_profile = tmp_path / "build-profile.json5"
    build_profile.write_text('{"modules": ["entry"]}', encoding="utf-8")
    graph = gm.backend.native_graph
    fallback_edges = graph.get_edge_data("module:demo.app", "ext_lib:hv-lib@1.0.0") or {}
    assert len(fallback_edges) == 1

    parser.parse(build_profile)  # flushes pending deps to entry

    # Entry module should get the dependency
    assert graph.has_edge("module:entry", "ext_lib:hv-lib@1.0.0")
    # Fallback module edge count should remain single (no duplicate)
    fallback_edges_after = graph.get_edge_data("module:demo.app", "ext_lib:hv-lib@1.0.0") or {}
    assert len(fallback_edges_after) == 1


def test_inferred_root_lock_not_replayed_on_flush(tmp_path: Path) -> None:
    """Fallback root module for lockfile should not be processed twice."""
    lock_cfg = tmp_path / "oh-package-lock.json5"
    lock_cfg.write_text(
        '{"packages": {"node_modules/libfoo": {"name": "libfoo", "version": "1.2.3", "registryType": "ohpm"}}}',
        encoding="utf-8",
    )

    gm = GraphManager(root_path=tmp_path)
    parser = HvigorParser(tmp_path, gm, EventBus())

    parser.parse(lock_cfg)  # creates inferred module based on workspace
    build_profile = tmp_path / "build-profile.json5"
    build_profile.write_text('{"modules": ["entry"]}', encoding="utf-8")
    graph = gm.backend.native_graph
    module_ids = [
        nid for nid, attrs in gm.nodes() if attrs.get("type") == NodeType.MODULE.value
    ]
    assert len(module_ids) == 1
    fallback_module = module_ids[0]
    fallback_edges = graph.get_edge_data(fallback_module, "ext_lib:libfoo@1.2.3") or {}
    assert len(fallback_edges) == 1

    parser.parse(build_profile)

    assert graph.has_edge("module:entry", "ext_lib:libfoo@1.2.3")
    fallback_edges_after = graph.get_edge_data(
        fallback_module, "ext_lib:libfoo@1.2.3"
    ) or {}
    assert len(fallback_edges_after) == 1
