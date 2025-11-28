from pathlib import Path

from depanalyzer.graph import EdgeKind, GraphManager
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
