# CLI Reference

Complete reference for all Depanalyzer command-line options.

For option semantics (cache layout, merged exports, ID namespacing), see [Usage Guide](usage.md).

## Global Options

These options apply to all commands:

| Option | Description |
|--------|-------------|
| `-v, --verbose` | Enable verbose (debug) logging |
| `--install [VERSION]` | Download and install ScanCode Toolkit locally (default: 32.4.1) |

## Commands

- [`scan`](#scan) - Analyze project dependencies
- [`export`](#export) - Export cached graphs (JSON views)
- [`scancode`](#scancode) - Run license detection
- [`dag`](#dag) - Validate dependency graph for cycles
- `query` - Placeholder (not implemented)
- `explain` - Placeholder (not implemented)

---

## scan

Analyze a project and generate a dependency graph.

```bash
depanalyzer scan <source> -o <output> [options]
```

### Required Arguments

| Argument | Description |
|----------|-------------|
| `source` | Local path or Git URL to analyze |
| `-o, --output` | Output graph file (JSON) |

### Optional Arguments

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--cache-dir` | path | `.dep_cache` | Base directory for scan cache (writes `<cache-dir>/<source_stem>/{sources,graphs}`) |
| `-w, --workers` | int | 8 | Maximum concurrent workers |
| `-d, --max-depth` | int | 3 | Maximum third-party dependency depth (only with `-t`) |
| `--max-deps` | int | - | Global third-party dependency limit (only with `-t`) |
| `--concurrent-deps` | int | 4 | Maximum concurrent dependency transactions |
| `-t, --third-party` | flag | false | Enable third-party dependency resolution |
| `--no-analyze` | flag | false | Skip analysis phase |
| `--fallback-tree` | flag | false | Enable fallback tree for unparsed files |
| `-c, --config` | path/string | - | Configuration file (TOML/JSON) or inline config |
| `--timeout` | int | 900 | Reserved for future global timeout enforcement (task-level timeouts still apply) |
| `--log-file` | path | - | Output log to file |

### Examples

**Basic scan:**
```bash
depanalyzer scan /path/to/project -o graph.json
```

**Scan with third-party dependencies:**
```bash
depanalyzer scan /path/to/project -o graph.json --third-party --max-depth 3
```

**High-performance scan:**
```bash
depanalyzer scan /path/to/project -o graph.json -w 16 --concurrent-deps 8
```

**Scan with custom configuration:**
```bash
depanalyzer scan /path/to/project -o graph.json --config config.toml
```

**Scan with inline configuration:**
```bash
depanalyzer scan /path/to/project -o graph.json --config '{"fallback": {"enabled": true}}'
```

**Scan with fallback tree for license compliance:**
```bash
depanalyzer scan /path/to/project -o graph.json --fallback-tree
```

**Skip analysis for faster output:**
```bash
depanalyzer scan /path/to/project -o graph.json --no-analyze
```

---

## export

Export cached graphs as JSON views.

```bash
depanalyzer export <graph_id> -o <output> [options]
```

### Required Arguments

| Argument | Description |
|----------|-------------|
| `graph_id` | Graph ID from scan output |
| `-o, --output` | Output file path |

### Optional Arguments

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--work-dir` | path | `.depanalyzer_cache/graphs` | If provided, treated as a project cache root and the command reads `<work-dir>/graphs` (for `scan` defaults: `.dep_cache/<source_stem>`) |
| `-f, --format` | string | `json` | Output format (`json` or `asset_artifact`) |
| `--with-deps` | flag | false | Include dependency graphs in export |

### Supported Formats

| Format | Extension | Description |
|--------|-----------|-------------|
| `json` | .json | JSON bundle containing one or more cached graphs |
| `asset_artifact` | .json | Asset-to-artifact projection view |

### Examples

**Export with dependencies:**
```bash
depanalyzer export my_graph_id -o full_graph.json --with-deps
```

**Export asset→artifact mapping:**
```bash
depanalyzer export my_graph_id -o asset_artifact.json --format asset_artifact
```

---

## scancode

Run ScanCode license detection on a project or cached graph.

```bash
depanalyzer scancode -o <output> [options]
```

### Required Arguments

| Argument | Description |
|----------|-------------|
| `-o, --output` | Output JSON file for license map |

### Optional Arguments

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--path` | path | - | Scan this directory directly (no cache needed) |
| `--cache-dir` | path | `.dep_cache` | Cache directory from previous scan (can point to `<cache-dir>/<source_stem>` or `.../graphs`) |
| `--source` | path | - | Source path from the scan command (used to derive `<source_stem>`) |
| `-t, --third-party` | flag | false | Include third-party dependency graphs |
| `--force` | flag | false | Force re-scan even if cached results exist |

### Examples

**Direct directory scan:**
```bash
depanalyzer scancode --path /path/to/project -o license_map.json
```

**Scan using cached graph:**
```bash
# First, run a scan
depanalyzer scan /path/to/project -o graph.json --third-party

# Then run scancode using the cache
depanalyzer scancode --source /path/to/project --third-party -o license_map.json
```

**Force re-scan:**
```bash
depanalyzer scancode --source /path/to/project --force -o license_map.json
```

### Output Format

The license map is a JSON object mapping node IDs to SPDX license expressions:

```json
{
  "//src/main.cpp": "MIT",
  "//lib/utils.h": "Apache-2.0",
  "//vendor/lib.c": "GPL-2.0-only OR MIT"
}
```

---

## dag

Validate the global dependency graph for cycles.

```bash
depanalyzer dag [options]
```

### Optional Arguments

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--cache-dir` | path | `.dep_cache` | Project cache root from previous scan (typically `.dep_cache/<source_stem>`) |
| `--limit` | int | 20 | Maximum number of cycles to report (<=0 for no limit) |
| `--fail-on-cycle` | flag | false | Exit with non-zero status if cycles found |

### Examples

**Check for cycles:**
```bash
depanalyzer dag
```

**Fail CI on cycles:**
```bash
depanalyzer dag --fail-on-cycle
```

**Report all cycles:**
```bash
depanalyzer dag --limit 0
```

---

## Common Workflows

### Basic Dependency Analysis

```bash
# Scan project
depanalyzer scan ./my-project -o graph.json

# View in graph explorer
python scripts/graph_explorer_tui.py graph.json
```

### Full License Compliance Pipeline

```bash
# 1. Scan with third-party deps and fallback tree
depanalyzer scan ./my-project -o graph.json -t --fallback-tree

# 2. Run license detection
depanalyzer scancode --source ./my-project -t -o license_map.json

# 3. Check compatibility (requires liscopelens)
python scripts/run_license_compatibility.py \
  --project-path ./my-project \
  --output-dir ./results
```

### CI/CD Integration

```bash
#!/bin/bash
set -e

# Scan project
depanalyzer scan . -o graph.json -t -w 8

# Check for dependency cycles
depanalyzer dag --fail-on-cycle

# Run license scan
depanalyzer scancode --source . -t -o license_map.json

# Validate licenses (custom script)
python check_licenses.py license_map.json
```

### Batch Processing Multiple Projects

```bash
# Using the pipeline script
python scripts/run_license_compatibility.py \
  --projects-dir /path/to/projects \
  --output-dir ./results \
  --third-party \
  --workers 16
```

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Command failed |
| 2 | Invalid arguments (argparse) |
| 130 | Interrupted (Ctrl+C, scan) |

---

## Environment Variables

See [Configuration](configuration.md#environment-variables) for environment variables that affect CLI behavior.
