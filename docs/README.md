# DepAnalyzer Architecture & Concepts

> ⚠️ Public API, registries, and config shapes are still evolving. Expect breaking changes between releases until the stability window is announced.

## Core Philosophy: Compliance Analysis vs. Vulnerability Mining

DepAnalyzer is designed specifically for **software supply chain compliance**. This imposes a different set of constraints compared to traditional static analysis tools used for bug finding or vulnerability mining.

*   **Vulnerability Mining (Traditional):** Prioritizes **Precision**.
    *   *Goal:* Find actionable bugs.
    *   *Trade-off:* It is better to miss a bug (False Negative) than to annoy the user with thousands of incorrect alerts (False Positive).
    *   *Approach:* "If I'm not sure a path is taken, assume it isn't."

*   **Compliance Analysis (DepAnalyzer):** Prioritizes **Recall (Completeness)**.
    *   *Goal:* Ensure no proprietary or GPL-licensed code is statically linked into a distributed binary without knowledge.
    *   *Trade-off:* It is acceptable to have **False Positives** (flagging a link that doesn't exist at runtime) but unacceptable to have **False Negatives** (missing a link that strictly creates a legal obligation).
    *   *Approach:* **"Over-estimation"**. If a dependency *might* exist (e.g., dynamic loading, wildcard includes, conditional compilation), DepAnalyzer assumes it *does* exist until proven otherwise.

## Architecture Overview

DepAnalyzer operates on a strictly phased lifecycle that builds a directed graph representing the relationship between **Assets** (Source files, Configs) and **Artifacts** (Binaries, Libraries, Packages).

### The Graph Data Model

The core data structure is managed by `GraphManager`. Nodes represent entities in the build process, and edges represent relationships.

*   **Nodes:** `file`, `target` (cmake target, npm package), `proxy` (external reference).
*   **Edges:** `depends_on`, `includes`, `links`, `generates`.

### Uncertainty Modeling

To support the "Over-estimation" philosophy, every node and edge supports an **Uncertainty Category**:

1.  **Definite:** We are 100% sure this relationship exists (e.g., explicit `#include "foo.h"` where `foo.h` exists locally).
2.  **Probable:** Heuristics suggest a link (e.g., naming conventions match, `derived_from` build contracts).
3.  **Conditional:** The link exists but depends on runtime factors or external configurations (e.g., `dlopen`, `proxy` nodes, unresolved external deps).

## The Runtime Lifecycle (7 Phases)

The `PhaseOrchestrator` (`depanalyzer/runtime/orchestrator.py`) manages the execution flow. Each phase is atomic and modifies the shared `TransactionState`.

1.  **ACQUIRE:**
    *   Downloads source code from git URLs or prepares local directories.
    *   Sets up the workspace.

2.  **DETECT:**
    *   Scans the workspace for "Target" files (e.g., `CMakeLists.txt`, `package.json`, `build.gradle`).
    *   Uses `BaseDetector` implementations.

3.  **PARSE:**
    *   **Config Parsing:** detailed parsing of detected build files (`BaseParser`).
    *   **Code Parsing:** Parallel processing of source files (C++, TS, etc.) to find granular dependencies (`BaseCodeParser`).

4.  **RESOLVE_DEPS:**
    *   Identifies external dependencies found during parsing.
    *   Uses `BaseDepFetcher` to download/clone external libraries into a cache.
    *   *Note:* This enables "Recursive Expansion" without unlimited recursion depth.

5.  **JOIN:**
    *   The "Linker" phase (`BaseLinker`).
    *   Connects isolated sub-graphs (e.g., linking a C++ `target` node to the specific `.cpp` files it compiles).
    *   Bridges different ecosystems (e.g., a Node.js Native Module linking to a C++ library).

6.  **ANALYZE:**
    *   Runs post-processing algorithms.
    *   **Uncertainty Analysis:** Applies structural rules to tag edges as `Probable` or `Conditional`.
    *   **Dead Code Elimination:** (Conservative implementation) identifies reachable vs. unreachable nodes.

7.  **EXPORT:**
    *   Flushes the final graph to disk.
    *   Generates reports.

## Extensibility & Policies

DepAnalyzer is built to be extended without modifying the core runtime.

### 1. Adding an Ecosystem (Parsers)
To support a new language or package manager, implement the interfaces in `depanalyzer/parsers/base.py`.

**Example: Adding support for "MyLang"**

```python
from pathlib import Path
from typing import List

from depanalyzer.parsers.base import BaseCodeParser, BaseDetector, BaseParser


class MyLangDetector(BaseDetector):
    NAME = "mylang_detector"
    ECOSYSTEM = "mylang"

    def detect(self) -> List[Path]:
        # Find build.mylang files under the workspace root
        return list(self.workspace_root.rglob("build.mylang"))


class MyLangParser(BaseParser):
    NAME = "mylang_parser"
    ECOSYSTEM = "mylang"

    def parse(self, target_path: Path) -> None:
        # 1. Parse the file
        content = target_path.read_text()

        # 2. Create graph nodes
        node_id = f"mylang:{target_path.parent.name}"
        self.add_node(node_id, "mylang_package", path=str(target_path))

        # 3. Emit events or add edges directly
        # ...


class MyLangCodeParser(BaseCodeParser):
    NAME = "mylang_code_parser"
    ECOSYSTEM = "mylang"
    CODE_GLOBS = ["**/*.ml"]

    def parse_file(self, file_path: Path) -> dict:
        imports = _parse_mylang_imports(file_path.read_text())
        return {
            "ecosystem": "mylang",
            "parser_name": self.NAME,
            "file": str(file_path),
            "imports": imports,
        }
```

Key interfaces:
*   `BaseDetector`: How to find the project files.
*   `BaseParser`: How to read the project structure.
*   `BaseCodeParser`: Process-pool parser for fine-grained code imports/includes.
*   `BaseCodeDependencyMapper`: Maps a code-parse result into graph edges for an ecosystem.
*   `BaseDepFetcher`: How to download dependencies (git, npm, maven, etc.).
*   `BaseLinker`: How to wire the graph together during JOIN.

**Ecosystem plugin checklist**
*   Implement Detector + config Parser + CodeParser + DepFetcher + Linker + CodeDependencyMapper; handle malformed inputs without crashing.
*   Add a config model/factory (for `[ecosystems.mylang.*]` blocks) and register it via `EcosystemConfigRegistry.register_config_factory("mylang", MylangEcosystemConfig.from_dict)`.
*   Register the ecosystem at import time with `register_ecosystem(...)` (see `depanalyzer/parsers/__init__.py` for the wiring pattern).
*   Forward `GraphBuildConfig` slices (`detect`, `parser`, `code_parser`, `linker`) into constructors so user-provided config takes effect.
*   Normalize node IDs/paths with `GraphManager.normalize_path` when possible to keep graphs stable across runs.

### 2. Runtime Registration & Configuration

All runtime wiring flows through registries to keep the orchestrator decoupled from ecosystems.

```python
from depanalyzer.parsers.registry import register_ecosystem
from depanalyzer.runtime.ecosystem_config_registry import EcosystemConfigRegistry

register_ecosystem(
    ecosystem="mylang",
    detector_class=MyLangDetector,
    parser_class=MyLangParser,
    fetcher_class=MyLangDepFetcher,
    code_parser_class=MyLangCodeParser,
    linker_class=MyLangLinker,
)
EcosystemConfigRegistry.register_config_factory(
    "mylang", MylangEcosystemConfig.from_dict
)
```

*   The CLI imports `depanalyzer.parsers`, which performs registration for built-in ecosystems; custom ecosystems should follow the same pattern.
*   `GraphBuildConfig` carries per-phase config slices. Access them with `graph_build_config.get_parser_config("mylang")` or the detector/code_parser/linker helpers, instead of reading raw dicts.
*   `GraphBuildConfig.contract_match`, `uncertainty`, `fallback`, and `projection` toggles are shared knobs used by JOIN/ANALYZE/EXPORT helpers—respect them in custom logic.

### 3. Event Bus & Dependency Resolution

Parsers should publish facts, not wire cross-domain edges directly.

*   Use `EventBus` (`TransactionContext.eventbus`) to publish detection, parse, and dependency events. Hooks or collectors subscribe and mutate the graph.
*   Emit `DEPENDENCY_DISCOVERED` with a `DependencySpec` so `DependencyCollector` can hand it to RESOLVE_DEPS. Keep this payload small and serializable.
*   Implement a `BaseDepFetcher` to pull third-party artifacts into `dep_cache_root`; defer heavyweight downloads to RESOLVE_DEPS rather than PARSE.
*   Prefer emitting evidence (paths, module names) in event payloads instead of immediate edges—this keeps parsers side-effect light and JOIN policies deterministic.

### 4. Linking & Contracts

The JOIN phase fuses per-ecosystem graphs and cross-language contracts.

*   `BaseLinker` implementations run after parsing; they should be idempotent and tolerate partial graphs.
*   Cross-language bridges flow through `ContractRegistry` (`depanalyzer/graph/contract_registry.py`). Parsers register `BuildInterfaceContract` instances; JOIN matches them using `ContractMatchConfig` and emits edges between provider/consumer artifacts.
*   Use join policies (e.g., `FileCompletenessJoinPolicy`) for fallback wiring instead of modifying linkers for unrelated concerns.

### 5. Code Parsing & Dependency Mapping

*   `BaseCodeParser.parse_file` must be a pure function (safe for process pools) and should tag results with `ecosystem` and `parser_name`.
*   Scope parsing with `CODE_GLOBS` to avoid scanning the entire tree unnecessarily.
*   Convert parse results into graph edges with `BaseCodeDependencyMapper` or a custom `CodeDependencyMapper`. Register it in the `Transaction` (via `create_transaction(..., code_dependency_mappers={"mylang": MyLangMapper()})`) so PARSE can dispatch correctly.
*   Shape parse results like `{"file": "...", "includes" or "imports": [...], "exports": [...], "ecosystem": "mylang"}` so mappers stay generic.

### 6. Lifecycle Hooks & Custom Policies

Behavior can be tuned using hooks and policies in `depanalyzer/runtime/policies`.

**Example: A Lifecycle Hook to log graph size**

```python
from depanalyzer.runtime.policies import LifecycleHook
from depanalyzer.runtime.lifecycle import LifecyclePhase


class GraphStatsHook(LifecycleHook):
    phase = LifecyclePhase.JOIN

    def before(self, ctx: TransactionContext) -> None:
        print(f"Before JOIN: {ctx.graph.node_count()} nodes")

    def after(self, ctx: TransactionContext) -> None:
        print(f"After JOIN: {ctx.graph.node_count()} nodes")
```

Available Policy Types:
*   **LifecycleHook:** Execute logic `before` or `after` any phase (ACQUIRE→EXPORT).
*   **CodeDependencyMapper:** Customize how code parse results (imports) are converted into graph edges.
*   **AssetProjectionPolicy:** Define how source assets map to output artifacts (critical for license compliance).
*   **JoinPolicy / AnalyzePolicy:** Add ecosystem-agnostic passes during JOIN or ANALYZE.
*   **UncertaintyPolicy (config):** Tune confidence thresholds for what constitutes a "Definite" link via `GraphBuildConfig.uncertainty`.

Inject hooks/policies when constructing a transaction (`create_transaction(..., lifecycle_hooks=[...], join_policies=[...])`) to keep the core runtime stable.

### 7. Testing Extensions

*   Add focused fixtures under `tests/<ecosystem>/` and run `poetry run pytest tests/<ecosystem>` to exercise detectors, parsers, and linkers together.
*   Smoke test end-to-end with `poetry run depanalyzer scan <path> -o graph.json`; inspect the graph for node/edge IDs, contract matches, and projection edges.
*   Validate degraded paths: broken config files, missing dependencies, and empty workspaces should log warnings but keep the transaction alive.
