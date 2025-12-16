# Extension Guide

This guide explains how to extend Depanalyzer with new ecosystems (build systems), and how to plug into the runtime via hooks and policies.

## Quick Start: Add a New Ecosystem

An ecosystem typically consists of:

| Component | Required | Purpose |
|-----------|----------|---------|
| Detector | Yes | Find build configuration files (targets) |
| Parser | Yes | Parse targets and populate graph nodes/edges |
| DepFetcher | Yes | Fetch third-party dependencies into cache |
| CodeParser | Optional | Parse source code and emit import/include facts |
| Linker | Optional | Derive additional relationships after parsing |

**Workflow**

1. Create `depanalyzer/parsers/<ecosystem>/` and implement the components you need.
2. Register the ecosystem in `depanalyzer/parsers/__init__.py` via `register_ecosystem(...)`.
3. (Optional) Provide a config model and register a factory via `EcosystemConfigRegistry`.
4. Add tests under `tests/parsers/<ecosystem>/` and update `docs/configuration.md` + `docs/index.md`.

## Minimal Skeletons (Aligned With Current APIs)

### Detector

Detectors subclass `BaseDetector` and should prefer `scan_workspace()` which respects `.gitignore`.

```python
from pathlib import Path
from typing import List

from depanalyzer.parsers.base import BaseDetector
from depanalyzer.runtime.eventbus import Event, EventType


class MyDetector(BaseDetector):
    """Detector for MyBuildSystem targets."""

    NAME = "my_detector"
    ECOSYSTEM = "myeco"

    def detect(self) -> List[Path]:
        """Detect build targets for this ecosystem.

        Returns:
            List of build target file paths.
        """
        targets = self.scan_workspace(
            patterns=["**/mybuild.toml"],
            ignore_patterns=["**/.git/**", "**/node_modules/**"],
            recursive=True,
        )

        for target in targets:
            self.publish_detection_event(
                Event(
                    event_type=EventType.TARGET_DETECTED,
                    source=self.NAME,
                    data={"target_path": str(target), "ecosystem": self.ECOSYSTEM},
                )
            )

        return targets
```

### Parser

Parsers subclass `BaseParser`. Use `NodeType` and `EdgeKind` from `depanalyzer.graph`.
For path-like node types (e.g., `module`, `config`), include `src_path` so schema validation can succeed.

```python
import json
from pathlib import Path

from depanalyzer.graph import EdgeKind, NodeType
from depanalyzer.graph.models.identifiers import normalize_node_id
from depanalyzer.parsers.base import BaseParser, ConfigurationError


class MyParser(BaseParser):
    """Parser for MyBuildSystem target files."""

    NAME = "my_parser"
    ECOSYSTEM = "myeco"

    def parse(self, target_path: Path) -> None:
        """Parse one target file and populate the transaction graph.

        Args:
            target_path: Path to a build target detected by the detector.
        """
        try:
            payload = json.loads(target_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError(f"Invalid config {target_path}: {exc}") from exc

        module_dir = target_path.parent
        module_id = normalize_node_id(module_dir, str(self.workspace_root))

        self.add_node(
            module_id,
            NodeType.MODULE.value,
            parser_name=self.NAME,
            name=payload.get("name", module_dir.name),
            src_path=str(module_dir.resolve()),
            ecosystem=self.ECOSYSTEM,
        )

        for dep_name in payload.get("deps", []):
            dep_id = f"//external:{self.ECOSYSTEM}/{dep_name}"
            self.add_node(
                dep_id,
                NodeType.EXTERNAL_DEP.value,
                parser_name=self.NAME,
                name=dep_name,
                ecosystem=self.ECOSYSTEM,
                origin="external",
            )
            self.add_edge(
                module_id,
                dep_id,
                EdgeKind.DEPENDS_ON.value,
                parser_name=self.NAME,
            )
```

### Dependency Fetcher

Dependency fetchers subclass `BaseDepFetcher`. Use `get_cache_dir()` to compute stable cache paths and reuse helpers like `git_clone()`, `download_file()`, and `extract_archive()`.

```python
from pathlib import Path
from typing import Optional

from depanalyzer.parsers.base import BaseDepFetcher, DependencySpec


class MyDepFetcher(BaseDepFetcher):
    """DepFetcher for MyBuildSystem dependencies."""

    NAME = "my_dep_fetcher"
    ECOSYSTEM = "myeco"

    def can_handle(self, dep_spec: DependencySpec) -> bool:
        """Return True when this fetcher can handle the dependency spec."""
        return dep_spec.ecosystem == self.ECOSYSTEM

    def fetch(self, dep_spec: DependencySpec) -> Optional[Path]:
        """Fetch the dependency and return its local cache path."""
        cache_dir = self.get_cache_dir(dep_spec)
        if cache_dir.exists():
            return cache_dir

        # Implement download/clone here (example):
        # self.git_clone(dep_spec.source_url, cache_dir, depth=1)
        return cache_dir if cache_dir.exists() else None
```

### Code Parser + Mapper (Optional)

If you want code-level dependencies:

- Implement a `BaseCodeParser` with a pure `parse_file(Path) -> dict` method. It runs in a process pool.
- Implement a `CodeDependencyMapper` (or subclass `BaseCodeDependencyMapper`) to map parse results into graph edges during the PARSE phase.

See `depanalyzer/parsers/*/code_parser.py` and `depanalyzer/parsers/*/code_dependency_mapper.py` for patterns.

### Linker (Optional)

Linkers subclass `BaseLinker` and run during JOIN. They should be idempotent and robust to partial graphs.

```python
from depanalyzer.parsers.base import BaseLinker


class MyLinker(BaseLinker):
    """Linker for MyBuildSystem derived relationships."""

    ECOSYSTEM = "myeco"

    def link(self) -> None:
        """Apply linking logic to the current transaction graph."""
        return
```

## Registering the Ecosystem

Ecosystems are registered at import time via `depanalyzer/parsers/__init__.py` (the CLI imports `depanalyzer.parsers` during startup).

Use the convenience helper:

```python
from depanalyzer.parsers.registry import register_ecosystem
from depanalyzer.runtime.ecosystem_config_registry import EcosystemConfigRegistry

from depanalyzer.parsers.myeco.detector import MyDetector
from depanalyzer.parsers.myeco.config_parser import MyParser
from depanalyzer.parsers.myeco.dep_fetcher import MyDepFetcher

register_ecosystem(
    ecosystem="myeco",
    detector_class=MyDetector,
    parser_class=MyParser,
    fetcher_class=MyDepFetcher,
    code_parser_class=None,
    linker_class=None,
)
```

## Configuration (Optional, Recommended)

To make your ecosystem configurable via `[ecosystems.<name>.*]`, define a config model with a `from_dict()` factory and register it:

```python
EcosystemConfigRegistry.register_config_factory("myeco", MyEcosystemConfig.from_dict)
```

Then users can write:

```toml
[ecosystems.myeco.detect]
ignore_dirs = ["build", "dist"]

[ecosystems.myeco.parser]
include_dev_dependencies = false
```

At runtime, Depanalyzer passes these slices into your components via:

- `GraphBuildConfig.get_detect_config(ecosystem)`
- `GraphBuildConfig.get_parser_config(ecosystem)`
- `GraphBuildConfig.get_code_parser_config(ecosystem)`
- `GraphBuildConfig.get_linker_config(ecosystem)`

## Hooks and Policies (Runtime Extension)

Beyond ecosystems, the runtime supports extension via protocols in `depanalyzer/runtime/policies/protocols.py`:

- `LifecycleHook` (`before`/`after` a phase)
- `JoinPolicy` (JOIN phase)
- `AnalyzePolicy` (additional analysis passes)
- `AssetProjectionPolicy` (custom projection behavior)

Hooks/policies are provided when constructing a `Transaction` (or via `depanalyzer.runtime.api.create_transaction(...)`). The per-transaction `EventBus` is available as `ctx.eventbus` in lifecycle contexts.
