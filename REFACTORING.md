# Refactoring Summary

## Overview

This document summarizes the major refactoring performed on the depanalyzer project to align with the design specified in `AGENTS.md`. The refactoring implements a **single-transaction, unified-graph, detect→parse→join** architecture.

## Refactoring Date

**Completed:** 2025-11-13

## Key Architectural Changes

### 1. New Module Structure

The project now follows a modular architecture with clear separation of concerns:

```
depanalyzer/
├── runtime/           # Core transaction execution
│   ├── transaction.py # Single transaction coordinator
│   ├── workspace.py   # Source code acquisition
│   ├── worker.py      # Concurrent task executor
│   ├── eventbus.py    # Event bus for parser→hook communication
│   └── lifecycle.py   # Lifecycle phase definitions
├── graph/             # Graph management
│   ├── manager.py     # Single transaction graph manager
│   ├── backend.py     # NetworkX abstraction layer
│   ├── registry.py    # Global graph registry
│   ├── global_dag.py  # Package-level dependency DAG
│   └── proxy.py       # External graph references
├── parsers/           # Language parsers (detector + parser pattern)
│   └── base/          # Base detector and parser classes
├── hooks/             # Cross-domain association hooks
│   ├── hvigor_cmake_bridge.py    # Hvigor↔CMake associations
│   ├── packaging_bridge.py       # Artifact→package associations
│   ├── linkage_enrichment.py     # Linkage type enrichment
│   └── unresolved_marking.py     # Unresolved dependency marking
├── analysis/          # Analysis algorithms
│   ├── linkage.py     # Linkage type analysis
│   └── deadcode.py    # Dead code detection
├── export/            # Export formats
│   ├── json.py        # JSON export
│   ├── graphml.py     # GraphML export
│   └── dot.py         # DOT export
└── cli/               # New command-line interface
    ├── __main__.py    # Main CLI entry point
    ├── scan.py        # Scan command
    └── export.py      # Export command
```

### 2. Core Concepts

#### Single Transaction, Unified Graph

- Each analysis session is a **Transaction** that maintains exactly one **GraphManager** instance
- All nodes and edges for that session are written to the same graph
- Third-party dependencies spawn new transactions (with their own graphs)
- Parent graphs reference child graphs via **proxy nodes**, not by merging

#### Lifecycle Phases

Every transaction follows a fixed phase sequence:

1. **ACQUIRE**: Source code acquisition (local path or Git URL)
2. **DETECT**: Parallel detection of targets (Hvigor/CMake/Prebuilt/Deps)
3. **PARSE**: Parallel parsing of detected targets
4. **RESOLVE_DEPS**: Unified dependency resolution
5. **JOIN**: Hook execution for cross-domain associations
6. **ANALYZE**: Analysis on the transaction graph
7. **EXPORT**: Export results in various formats

#### Event Bus Architecture

- **Parsers** publish structured events (e.g., `TARGET_DETECTED`, `MODULE_PARSED`)
- **Hooks** subscribe to events and perform cross-domain associations
- This decouples parsers from hooks, enabling independent evolution

#### Detector + Parser Pattern

Parsers are split into two phases:

- **Detector**: Lightweight scanning to find parsing targets (嗅探)
- **Parser**: Detailed parsing and graph construction (解析)

#### Multi-抓手 (Multiple Handles) Associations

Hooks use multiple evidence sources to establish connections:

1. **Path抓手**: Directory containment (e.g., Hvigor native dir contains CMakeLists.txt)
2. **Artifact抓手**: Output artifact name/ABI matching
3. **Config抓手**: Toolchain/ABI consistency
4. **Declaration抓手**: Explicit configuration

Confidence scores are assigned based on the strength of evidence.

### 3. Key Design Principles (from AGENTS.md)

1. **No Recursion**: All phases use queue-based execution (Worker)
2. **Deduplication**: Task fingerprinting prevents duplicate work
3. **Conservative Over-Approximation**: When uncertain, mark with `over_approx=True` and provide evidence
4. **Graph Proxy Pattern**: Third-party graphs are referenced, not merged
5. **Idempotent Hooks**: Hooks can be executed multiple times without side effects

## Backward Compatibility

The old CLI interface (`app/run.py`) remains available for backward compatibility. The new CLI is accessed via:

```bash
depanalyzer scan <source> -o <output> [options]
```

## Migration Path

### For Users

- **Old CLI**: Still works via `app.run:main`
- **New CLI**: Use `cli.__main__:main` or `python -m cli`
- The `main.py` entry point now defaults to the new CLI

### For Developers

To migrate existing parsers to the new architecture:

1. Split parser into `Detector` and `Parser` classes inheriting from `parsers.base`
2. Implement `detect()` method to find targets
3. Implement `parse()` method to parse targets and publish events
4. Create hooks to subscribe to parser events and establish associations

### Example Migration

**Old parser pattern:**
```python
class MyParser(BaseParser):
    def parse_files(self, files):
        for file in files:
            # Parse and add to graph directly
            self.graph.add_node(...)
```

**New parser pattern:**
```python
class MyDetector(BaseDetector):
    def detect(self):
        # Find targets
        targets = find_targets(self.workspace_root)
        for target in targets:
            self.publish_detection_event(Event(...))
        return targets

class MyParser(BaseParser):
    def parse(self, target_path):
        # Parse target
        data = parse_target(target_path)
        # Add to graph
        self.add_node(...)
        # Publish events for hooks
        self.publish_parse_event(Event(...))
```

## Testing Status

⚠️ **Note**: The refactored architecture has been implemented but not yet fully tested. Comprehensive testing is required before production use.

### Next Steps

1. Implement detector and parser subclasses for Hvigor and CMake
2. Complete hook implementations with actual matching logic
3. Add unit tests for all new modules
4. Add integration tests for end-to-end workflows
5. Performance benchmarking against old architecture

## Documentation

All new modules include comprehensive docstrings following the coding guidelines in `AGENTS.md`:

- Module-level docstrings in English
- Function/method docstrings with Args, Returns sections
- Type hints throughout
- Logging using lazy formatting

## Benefits of New Architecture

1. **Clearer Separation of Concerns**: Runtime, graph, parsers, hooks, analysis, and export are cleanly separated
2. **Better Extensibility**: New parsers and hooks can be added without modifying core logic
3. **Event-Driven Design**: Parsers and hooks communicate via events, enabling loose coupling
4. **Scalable**: Queue-based execution and deduplication support large codebases
5. **Third-Party Isolation**: Each third-party dependency gets its own transaction and graph
6. **Confidence Tracking**: All associations track confidence and evidence
7. **Future-Proof**: Graph backend abstraction allows swapping NetworkX for other implementations

## Known Limitations

1. **Incomplete Implementations**: Many hooks and parsers are placeholder implementations
2. **No Query/Explain Commands**: CLI commands for querying and explaining graphs are not yet implemented
3. **Limited Analysis**: Linkage and deadcode analyzers need full implementations
4. **No Migration Utilities**: No automated tools to migrate from old to new architecture

## References

- `AGENTS.md`: Authoritative design specification
- `README.md`: User-facing documentation
- `pyproject.toml`: Project configuration and dependencies

## Additional Refactoring (2025-11-13)

### Graph Module Consolidation

**Migration:** `utils/graph.py` → `graph/legacy.py`

The legacy GraphManager implementation (1504 lines, NetworkX-based) has been moved from `utils/` to `graph/` module to consolidate all graph-related code in one place.

**Changes:**
- Moved `utils/graph.py` to `graph/legacy.py`
- Updated 9 import statements across the codebase:
  - `runtime/transaction.py`
  - `parsers/hvigor/code_parser.py`
  - `parsers/cpp/cmake/commands/*.py` (7 files)
- Deleted empty `utils/` directory

**Rationale:**
- Consolidates all graph functionality under `graph/` module
- `legacy.py` naming clearly indicates this is the old implementation
- Coexists with new `graph/manager.py` during gradual migration
- No circular import risks (verified)

**Status:**
- ✅ **All 9 files** have been migrated to use `graph.manager.GraphManager`
- ✅ **Complex projection algorithm** (`derive_asset_artifact_projection()`) ported to new GraphManager
- ✅ **graph/legacy.py deleted** - migration complete
- ✅ Migration completed on **2025-11-13**

**Migration Completed:**
1. ✅ Ported 462-line `derive_asset_artifact_projection()` method to new GraphManager
2. ✅ Ported `fuse_projection_evidence()` method to new GraphManager
3. ✅ Migrated all CMake command handlers (7 files):
   - `parsers/cpp/cmake/commands/base.py`
   - `parsers/cpp/cmake/commands/includes.py`
   - `parsers/cpp/cmake/commands/linking.py`
   - `parsers/cpp/cmake/commands/packages.py`
   - `parsers/cpp/cmake/commands/sources.py`
   - `parsers/cpp/cmake/commands/target.py`
   - `parsers/cpp/cmake/commands/variables.py`
4. ✅ Migrated `parsers/hvigor/code_parser.py`
5. ✅ Migrated `runtime/transaction.py`
6. ✅ Deleted `graph/legacy.py` (1504 lines)

**Key API Changes:**
- **Vertex/Edge wrappers removed**: Direct `add_node()` and `add_edge()` calls
- **Type parameter renamed**: `type` → `node_type`
- **Kind parameter unified**: `label`/`type`/`kind` → `edge_kind`
- **Built-in support**: `confidence`, `over_approx`, `evidence` now standard parameters
- **Cleaner API**: No wrapper object overhead, more maintainable code

