# Configuration Reference

Complete reference for all Depanalyzer configuration options.

## Configuration Methods

Depanalyzer can be configured through three methods (in order of precedence):

1. **CLI flags** - Highest priority, override all other settings
2. **Configuration files** - TOML or JSON files passed via `--config`
3. **Environment variables** - For ScanCode and system settings

## Configuration File Format

### File Types

| Extension | Format |
|-----------|--------|
| `.toml`, `.tml` | TOML format |
| `.json` | JSON format |

### Loading Configuration

```bash
# From file
depanalyzer scan . -o graph.json --config config.toml

# Inline JSON
depanalyzer scan . -o graph.json --config '{"fallback": {"enabled": true}}'

# Inline TOML (auto-detected if not starting with { or [)
depanalyzer scan . -o graph.json --config 'fallback.enabled = true'
```

---

## Configuration Sections

### [projection]

Controls asset-to-artifact projection for linking source files to build outputs.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enable_fallback` | bool | `true` | Enable fallback projection when direct links fail |
| `enable_link_closure` | bool | `false` | Compute transitive closure of link relationships |
| `enable_header_closure` | bool | `false` | Compute transitive closure of header includes |
| `max_header_hops` | int | `2` | Maximum hops for header closure |
| `link_closure_max_hops` | int | `3` | Maximum hops for link closure |
| `ignore_external_placeholders_in_fallback` | bool | `false` | Ignore external placeholders in fallback |
| `nearest_dir_min_evidence` | int | `0` | Minimum evidence for nearest-directory matching |
| `resolve_include_placeholders` | bool | `false` | Resolve include placeholders |
| `fuse_evidence` | bool | `false` | Fuse evidence from multiple sources |
| `fallback_max_targets_per_node` | int | `null` | Limit fallback targets per node |
| `fallback_disable_import_inherited` | bool | `false` | Disable import inheritance in fallback |
| `fallback_disable_nearest_dir` | bool | `false` | Disable nearest-directory fallback |

**Example:**
```toml
[projection]
enable_fallback = true
enable_link_closure = true
max_header_hops = 3
```

---

### [contract_match]

Controls build interface contract matching for cross-language linking.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enable_artifact_name` | bool | `true` | Match by artifact name (e.g., libfoo.so) |
| `enable_napi` | bool | `false` | Match NAPI bindings |
| `enable_extern_c` | bool | `false` | Match extern "C" symbols |
| `enable_path` | bool | `false` | Match by path containment |
| `enable_config` | bool | `false` | Match by build configuration |

**Example:**
```toml
[contract_match]
enable_artifact_name = true
enable_napi = true
enable_extern_c = true
```

---

### [uncertainty]

Controls uncertainty classification for dependency confidence levels.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enabled` | bool | `false` | Enable uncertainty analysis |
| `probable_min_confidence` | float | `0.6` | Minimum confidence for "probable" |
| `definite_min_confidence` | float | `0.9` | Minimum confidence for "definite" |
| `mark_projection_edges_as_probable` | bool | `true` | Mark projection edges as probable |
| `mark_contract_edges_as_probable` | bool | `true` | Mark contract edges as probable |
| `mark_proxy_nodes_as_conditional` | bool | `true` | Mark proxy nodes as conditional |
| `mark_unresolved_external_as_conditional` | bool | `true` | Mark unresolved externals as conditional |
| `mark_dynamic_patterns_as_conditional` | bool | `true` | Mark dynamic patterns as conditional |

**Example:**
```toml
[uncertainty]
enabled = true
probable_min_confidence = 0.7
definite_min_confidence = 0.95
```

---

### [fallback]

Controls fallback graph construction for unparsed files.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enabled` | bool | `false` | Enable fallback tree construction |
| `root_id` | string | `"//fallback/license_scan"` | Root node ID for fallback tree |
| `include_isolated_nodes` | bool | `true` | Connect isolated nodes to root |

**Example:**
```toml
[fallback]
enabled = true
root_id = "//fallback/license_scan"
include_isolated_nodes = true
```

**When to use:** Enable fallback when running license compliance checks to ensure all files are included in the analysis, even if they couldn't be parsed.

---

### [license_link]

Controls license file attachment to nodes.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enabled` | bool | `true` | Enable license file linking |
| `file_patterns` | list | see below | Patterns for license files |

**Default file patterns:**
```toml
file_patterns = [
    "LICENSE",
    "LICENSE.*",
    "COPYING",
    "COPYING.*",
    "NOTICE",
    "NOTICE.*"
]
```

**Example:**
```toml
[license_link]
enabled = true
file_patterns = ["LICENSE", "LICENSE.*", "COPYING", "MIT-LICENSE"]
```

---

## Per-Ecosystem Configuration

Each ecosystem can be configured independently under `[ecosystems.<name>]`.

### Hvigor Ecosystem

#### [ecosystems.hvigor.detect]

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `include_root_dirs` | list | `null` | Limit detection to specific directories |
| `ignore_node_modules` | bool | `true` | Skip node_modules directories |

#### [ecosystems.hvigor.parser]

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enable_native_dependencies` | bool | `true` | Parse native (C++) dependencies |
| `infer_missing_modules` | bool | `true` | Infer modules not explicitly declared |

#### [ecosystems.hvigor.code_parser]

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `max_import_ancestor_levels` | int | `8` | Max levels to search for import targets |

#### [ecosystems.hvigor.linker]

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `attach_code_without_module` | bool | `false` | Attach orphan code files |
| `auto_infer_entry_module` | bool | `false` | Auto-infer entry module |
| `enable_path_bridge` | bool | `true` | Enable path-based bridging |
| `enable_shared_library_cpp_link` | bool | `true` | Link shared libraries to C++ |

**Example:**
```toml
[ecosystems.hvigor.detect]
ignore_node_modules = true

[ecosystems.hvigor.parser]
enable_native_dependencies = true

[ecosystems.hvigor.linker]
enable_shared_library_cpp_link = true
```

---

### C++ Ecosystem

#### [ecosystems.cpp.detect]

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `ignore_build_dirs` | bool | `true` | Skip build directories |
| `ignore_third_party_dirs` | bool | `false` | Skip third_party directories |

#### [ecosystems.cpp.parser]

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `ignore_object_library_targets` | bool | `false` | Skip OBJECT library targets |
| `ignore_interface_library_targets` | bool | `false` | Skip INTERFACE library targets |

#### [ecosystems.cpp.code_parser]

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enable_experimental_features` | bool | `false` | Enable experimental parsing |

#### [ecosystems.cpp.linker]

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enable_linkage_enrichment` | bool | `true` | Enrich linkage information |

**Example:**
```toml
[ecosystems.cpp.detect]
ignore_build_dirs = true
ignore_third_party_dirs = true

[ecosystems.cpp.parser]
ignore_interface_library_targets = true
```

---

### npm Ecosystem

#### [ecosystems.npm.detect]

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `ignore_node_modules` | bool | `true` | Skip node_modules directories |
| `detect_workspaces` | bool | `true` | Detect npm workspaces |
| `extra_ignore_patterns` | list | `[]` | Extra glob patterns to ignore during detection |

#### [ecosystems.npm.parser]

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `include_dev_dependencies` | bool | `false` | Include devDependencies |
| `include_peer_dependencies` | bool | `false` | Include peerDependencies |
| `parse_lock_file` | bool | `true` | Parse package-lock.json |

#### [ecosystems.npm.code_parser]

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `parse_typescript` | bool | `true` | Parse TypeScript files |
| `max_import_ancestor_levels` | int | `8` | Max levels for import resolution |

#### [ecosystems.npm.linker]

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `link_workspace_packages` | bool | `true` | Link workspace packages |
| `infer_entry_from_main` | bool | `true` | Infer entry from package.json main |
| `enable_native_addon_link` | bool | `true` | Link native addons |

**Example:**
```toml
[ecosystems.npm.detect]
ignore_node_modules = true
detect_workspaces = true

[ecosystems.npm.parser]
include_dev_dependencies = true
parse_lock_file = true

[ecosystems.npm.linker]
link_workspace_packages = true
```

---

### Maven Ecosystem

#### [ecosystems.maven.detect]

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `ignore_dirs` | list | `null` | Optional directory names to ignore during detection |

#### [ecosystems.maven.parser]

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `include_test_scope` | bool | `false` | Include `test` scoped dependencies in the graph |
| `record_system_path` | bool | `true` | Record `systemPath` dependencies when present |

#### [ecosystems.maven.code_parser]

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enable_native_detection` | bool | `true` | Enable native-library heuristics in Java parsing |

#### [ecosystems.maven.linker]

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `attach_test_sources` | bool | `false` | Attach test sources to modules when enabled |

**Example:**
```toml
[ecosystems.maven.detect]
ignore_dirs = ["target", ".m2", "node_modules"]

[ecosystems.maven.parser]
include_test_scope = false
record_system_path = true
```

---

## Environment Variables

### ScanCode Configuration

| Variable | Description |
|----------|-------------|
| `SCANCODE_VERSION` | Override default ScanCode version (default: 32.4.1) |
| `SCANCODE_BIN` | Path to ScanCode executable (highest priority) |
| `SCANCODE_CLI` | Alternative path to ScanCode executable |
| `SCANCODE_TOOLKIT` | Path to ScanCode toolkit directory |
| `SCANCODE_BASE_URL` | Override download base URL |
| `SCANCODE_DOWNLOAD_URL` | Override complete download URL |

### ScanCode Discovery Order

1. `SCANCODE_BIN` environment variable
2. `SCANCODE_CLI` environment variable
3. `scancode` in system PATH
4. `SCANCODE_TOOLKIT` environment variable
5. Default install: `~/.depanalyzer/scancode-toolkit`
6. Current directory: `./scancode` or `./scancode.bat`
7. Current directory: `./scancode-toolkit/scancode`

**Example:**
```bash
# Linux/macOS
export SCANCODE_BIN=/opt/scancode-toolkit/scancode

# Windows PowerShell
$env:SCANCODE_BIN = "C:\tools\scancode-toolkit\scancode.bat"
```

---

### Depanalyzer Runtime

| Variable | Description |
|----------|-------------|
| `DEPANALYZER_DAG_CHECK_ON_WRITE` | When set to `0`/`false`/`no`, disables the GlobalDAG cycle check performed after each dependency edge write |

---

## Complete Configuration Examples

### Minimal TOML

```toml
[fallback]
enabled = true
```

### Standard Analysis

```toml
[projection]
enable_fallback = true
enable_link_closure = false

[fallback]
enabled = true
include_isolated_nodes = true

[ecosystems.npm.parser]
include_dev_dependencies = false

[ecosystems.cpp.detect]
ignore_build_dirs = true
```

### Full JSON Configuration

```json
{
  "projection": {
    "enable_fallback": true,
    "enable_link_closure": true,
    "max_header_hops": 3
  },
  "contract_match": {
    "enable_artifact_name": true,
    "enable_napi": true,
    "enable_extern_c": false
  },
  "uncertainty": {
    "enabled": true,
    "probable_min_confidence": 0.6,
    "definite_min_confidence": 0.9
  },
  "fallback": {
    "enabled": true,
    "root_id": "//fallback/license_scan",
    "include_isolated_nodes": true
  },
  "license_link": {
    "enabled": true,
    "file_patterns": ["LICENSE", "LICENSE.*", "COPYING"]
  },
  "ecosystems": {
    "hvigor": {
      "detect": {
        "ignore_node_modules": true
      },
      "parser": {
        "enable_native_dependencies": true
      },
      "linker": {
        "enable_shared_library_cpp_link": true
      }
    },
    "cpp": {
      "detect": {
        "ignore_build_dirs": true,
        "ignore_third_party_dirs": false
      },
      "linker": {
        "enable_linkage_enrichment": true
      }
    },
    "npm": {
      "parser": {
        "include_dev_dependencies": false,
        "parse_lock_file": true
      },
      "linker": {
        "link_workspace_packages": true
      }
    }
  }
}
```

### License Compliance Configuration

```toml
# Optimized for license scanning

[fallback]
enabled = true
root_id = "//fallback/license_scan"
include_isolated_nodes = true

[license_link]
enabled = true
file_patterns = [
    "LICENSE",
    "LICENSE.*",
    "COPYING",
    "COPYING.*",
    "NOTICE",
    "NOTICE.*",
    "MIT-LICENSE",
    "APACHE-LICENSE"
]

[projection]
enable_fallback = true

[ecosystems.npm.parser]
include_dev_dependencies = false

[ecosystems.cpp.detect]
ignore_build_dirs = true
```
