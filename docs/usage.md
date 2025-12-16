# Usage Guide

This guide explains Depanalyzer’s runtime behavior and the “why” behind each CLI option. For the full option list, see `docs/cli-reference.md`.

## Key Concepts

### Transaction, Graph ID, and Cache Layout

- One `scan` run creates one **transaction** and produces one **graph snapshot** identified by `graph_id`.
- By default, `scan` writes caches under `.dep_cache/<source_stem>/`:
  - `sources/`: acquired sources and fetched third-party workspaces
  - `graphs/`: exported graph JSON files plus SQLite registries (`registry.db`, `global_dag.db`)
- The exported graph JSON is a NetworkX node-link structure with top-level `nodes` and `edges` plus `metadata` (including `graph_id`).

### Third-Party Dependencies = More Graphs

When `--third-party` is enabled:

- Each third-party dependency is scanned as a **child transaction** with its own `graph_id` and cached JSON.
- A package-level **GlobalDAG** records graph-to-graph dependencies.
- `scan -t -o ...` exports a **merged graph** where dependency nodes are namespaced as `//dep/<namespace>/...` to avoid ID collisions.

## Commands and Semantics

### `scan`

```
depanalyzer scan <source> -o <output.json> [options]
```

**What it does**

- Builds a dependency construct graph for `<source>` (local path or Git URL).
- Always caches the per-transaction graph JSON under the scan cache.
- When `--third-party` is enabled, may additionally export a merged JSON graph.

**Important options**

- `--cache-dir`: Base cache directory. The scan uses `<cache-dir>/<source_stem>/{sources,graphs}`.
- `-w/--workers`: Concurrency cap used by internal pools (thread worker + process tasks).
- `-t/--third-party`: Enables third-party resolution (spawns child transactions and populates GlobalDAG).
- `-d/--max-depth`: Maximum recursion depth for third-party resolution (only meaningful with `-t`).
- `--max-deps`: Global cap on the number of resolved third-party dependencies (only meaningful with `-t`).
- `--concurrent-deps`: Upper bound for concurrently running dependency transactions.
- `-c/--config`: GraphBuildConfig (TOML/JSON file path or inline TOML/JSON string). CLI flags override config where applicable.
- `--fallback-tree`: Forces `fallback.enabled = true` in GraphBuildConfig to ensure all files are represented for license workflows.
- `--no-analyze`: Skips graph analysis work (projection/deadcode/linkage/uncertainty).
- `--timeout`: Reserved for future global timeout enforcement (task-level timeouts still apply).
- `--log-file`: Writes logs to a file in addition to console output.

**Outputs**

- Without `-t`, `-o` receives the per-transaction graph JSON.
- With `-t`, `-o` receives a merged graph JSON that includes all dependency graphs (namespaced).

### `export`

```
depanalyzer export <graph_id> -o <output.json> [--work-dir <cache_root>] [--with-deps] [--format json|asset_artifact]
```

**What it does**

- Reads cached graphs and writes an export view.
- `--work-dir` is treated as a *project cache root*; the command reads `<work-dir>/graphs` (for `scan` defaults this is `.dep_cache/<source_stem>`).
- When `--work-dir` is omitted, the export command uses the legacy default `.depanalyzer_cache/graphs` directly.

**Formats**

- `json`: A bundle JSON containing `graphs: [...]` (and `global_dag` when available).
- `asset_artifact`: A derived “asset → artifact” mapping view intended for downstream tooling.

### `scancode`

```
depanalyzer scancode -o <license_map.json> [--path <dir> | --cache-dir <cache> --source <src>] [-t] [--force]
```

**What it does**

- Runs ScanCode and emits a flat mapping `{ "<node_id>": "<SPDX expression>" }`.
- Supports two modes:
  - `--path`: scan a directory directly (no cache required)
  - cache-backed: derive cached graphs from `--cache-dir` (and optionally `--source`) and align IDs with merged export semantics

**Third-party alignment**

- With `-t`, the command will include dependency graphs and prefix node IDs using the same `//dep/<namespace>/...` convention as merged exports.
- Per-graph license results are cached as `graphs/<graph_id>.licenses.json`.

### `dag`

```
depanalyzer dag [--cache-dir <project_cache_root>] [--limit N] [--fail-on-cycle]
```

**What it does**

- Inspects the package-level GlobalDAG (`graphs/global_dag.db`) and reports dependency cycles across scanned graphs.
- For scan caches, pass `--cache-dir .dep_cache/<source_stem>` so the command reads `.dep_cache/<source_stem>/graphs/global_dag.db`.

## Known Limitations

- `query` and `explain` subcommands exist as placeholders but are not implemented.
