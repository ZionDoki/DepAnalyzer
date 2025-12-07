"""Tests for depanalyzer CLI entrypoints."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import depanalyzer.main as main
from depanalyzer.cli import dag as dag_module
from depanalyzer.cli import export as export_module


def _stub_registry(ecosystems: list[str]) -> object:
    """Create a stub ecosystem registry exposing list_ecosystems."""

    class _Registry:
        def list_ecosystems(self) -> list[str]:
            return ecosystems

    class _Factory:
        @classmethod
        def get_instance(cls, *args, **kwargs):  # noqa: D401 - signature parity
            return _Registry()

    return _Factory


def test_main_dispatches_scan_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify that `main` parses args and dispatches scan_command."""

    monkeypatch.setattr(main, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(main, "EcosystemRegistry", _stub_registry(["cpp"]))

    captured: dict[str, object] = {}

    def fake_scan_command(args) -> int:
        captured["args"] = args
        return 0

    monkeypatch.setattr(main, "scan_command", fake_scan_command)

    argv = [
        "depanalyzer",
        "scan",
        str(tmp_path),
        "-o",
        str(tmp_path / "graph.json"),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    exit_code = main.main()

    assert exit_code == 0
    parsed = captured["args"]
    assert parsed.source == str(tmp_path)
    assert parsed.output == str(tmp_path / "graph.json")


def test_main_requires_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ensure missing subcommands make the CLI print help and fail."""

    monkeypatch.setattr(main, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(main, "EcosystemRegistry", _stub_registry([]))
    monkeypatch.setattr(sys, "argv", ["depanalyzer"])

    exit_code = main.main()

    assert exit_code == 1
    help_output = capsys.readouterr().out
    assert "Depanalyzer" in help_output


def _patch_graph_registry(
    monkeypatch: pytest.MonkeyPatch, module, registry
) -> None:
    """Patch GraphRegistry.get_instance for a CLI module."""

    monkeypatch.setattr(
        module.GraphRegistry,
        "get_instance",
        classmethod(lambda cls, cache_root=None: registry),
    )


def _patch_global_dag(monkeypatch: pytest.MonkeyPatch, module, dag) -> None:
    """Patch GlobalDAG.get_instance for a CLI module."""

    monkeypatch.setattr(
        module.GlobalDAG,
        "get_instance",
        classmethod(lambda cls, cache_dir=None: dag),
    )


def test_export_command_combines_main_and_dependencies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The export CLI should bundle dependency graphs when requested."""

    cache_dir = tmp_path / "cache"
    graphs_root = cache_dir / "graphs"
    graphs_root.mkdir(parents=True, exist_ok=True)

    (graphs_root / "global_dag.db").write_text(
        json.dumps({"id": "dag"}, indent=2), encoding="utf-8"
    )

    main_graph_path = graphs_root / "root.json"
    dep_graph_path = graphs_root / "dep.json"
    for path, graph_id in [
        (main_graph_path, "root"),
        (dep_graph_path, "dep"),
    ]:
        path.write_text(
            json.dumps(
                {
                    "graph": {"graph": {}, "nodes": [], "links": []},
                    "metadata": {"graph_id": graph_id},
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    class FakeRegistry:
        def __init__(self) -> None:
            self._paths = {"root": main_graph_path, "dep": dep_graph_path}

        def get_cache_path(self, graph_id: str):
            return self._paths.get(graph_id)

    class FakeDag:
        def get_transitive_dependencies(self, graph_id: str) -> list[str]:
            return ["dep"] if graph_id == "root" else []

    fake_registry = FakeRegistry()
    fake_dag = FakeDag()
    _patch_graph_registry(monkeypatch, export_module, fake_registry)
    _patch_global_dag(monkeypatch, export_module, fake_dag)

    output_file = tmp_path / "export.json"
    args = SimpleNamespace(
        graph_id="root",
        output=str(output_file),
        format="json",
        with_deps=True,
        work_dir=str(cache_dir),
    )

    exit_code = export_module.export_command(args)

    assert exit_code == 0
    data = json.loads(output_file.read_text(encoding="utf-8"))
    assert data["graph_count"] == 2
    assert {g["metadata"]["graph_id"] for g in data["graphs"]} == {"root", "dep"}
    assert data["global_dag"]["id"] == "dag"


def test_dag_command_respects_fail_on_cycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The DAG command should return non-zero when fail_on_cycle is set."""

    class FakeRegistry:
        def get_entry(self, graph_id: str):
            return {"summary": {"source": f"{graph_id}-src"}}

    class FakeDag:
        def get_cycles(self, limit=None):
            return [["root", "dep"]]

    fake_registry = FakeRegistry()
    fake_dag = FakeDag()
    _patch_graph_registry(monkeypatch, dag_module, fake_registry)
    _patch_global_dag(monkeypatch, dag_module, fake_dag)

    args_fail = SimpleNamespace(
        cache_dir=str(tmp_path),
        limit=5,
        fail_on_cycle=True,
    )
    args_ignore = SimpleNamespace(
        cache_dir=str(tmp_path),
        limit=0,
        fail_on_cycle=False,
    )

    assert dag_module.dag_command(args_fail) == 1
    assert dag_module.dag_command(args_ignore) == 0
