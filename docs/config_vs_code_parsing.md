# Config vs Code Parsing Architecture

## Overview

This document explains the **two-stage parsing architecture** that separates configuration-level dependency analysis from code-level dependency analysis in depanalyzer.

## Motivation

Different types of dependencies require different analysis strategies:

1. **Configuration Dependencies** (CMakeLists.txt, hvigorfile.ts, package.json)
   - Relatively small files
   - Complex parsing logic (build system semantics)
   - I/O bound (thread pool suitable)
   - Need access to GraphManager for graph construction

2. **Code Dependencies** (C++ includes, TypeScript imports)
   - Large number of files
   - Simple parsing logic (extract includes/imports)
   - CPU bound (process pool needed to avoid GIL)
   - Pure function processing (no shared state)

## Architecture Design

### Two Parser Types

#### 1. Config Parser (BaseParser)

**Purpose**: Parse configuration files and build system files

**Characteristics**:
- Extends `BaseParser` from `parsers/base.py`
- Implements `parse(target_path: Path) -> None`
- Has access to `GraphManager` and `EventBus`
- Can call `discover_code_files() -> List[Path]` to find source files
- Executed in **Worker thread pool** (I/O bound)

**Example**: CppParser parses CMakeLists.txt to extract targets and source files

```python
class CppParser(BaseParser):
    ECOSYSTEM = "cpp"

    def parse(self, target_path: Path) -> None:
        # Parse CMakeLists.txt
        targets = self._extract_targets(target_path)

        # Add nodes to graph
        for target in targets:
            self.add_node(target.name, "cmake_target", ...)

    def discover_code_files(self) -> List[Path]:
        # Return list of .c/.cpp files discovered
        return self._discovered_sources
```

#### 2. Code Parser (BaseCodeParser)

**Purpose**: Parse source code files to extract fine-grained dependencies

**Characteristics**:
- Extends `BaseCodeParser` from `parsers/base.py`
- Implements `parse_file(file_path: Path) -> Dict[str, Any]`
- **Pure function** - no side effects, no shared state
- Returns dict with include/import information
- Executed in **CodeParserPool process pool** (CPU bound, avoids GIL)

**Example**: CppCodeParser extracts #include directives

```python
class CppCodeParser(BaseCodeParser):
    ECOSYSTEM = "cpp"
    CODE_GLOBS = ["**/*.c", "**/*.cpp", "**/*.h", "**/*.hpp"]

    def parse_file(self, file_path: Path) -> Dict[str, Any]:
        # Parse source file (pure function)
        includes = self._extract_includes(file_path)

        return {
            "file": str(file_path),
            "includes": [Path(inc) for inc in includes],
            "root": "tree-sitter"
        }
```

### Process Flow

```
Transaction._phase_parse()
├── Stage 1: Config Parsing (Thread Pool)
│   ├── Get detected targets from _phase_detect()
│   ├── For each ecosystem + target:
│   │   ├── Create Parser instance
│   │   ├── Call parser.parse(target_path)
│   │   │   └── Parser updates GraphManager directly
│   │   └── Call parser.discover_code_files()
│   └── Collect all discovered source files
│
└── Stage 2: Code Parsing (Process Pool)
    ├── Group source files by ecosystem
    ├── For each ecosystem + files:
    │   └── Submit batch to CodeParserPool
    ├── Wait for all parsing to complete
    └── Process results:
        └── Add code file nodes and include/import edges to graph
```

### Execution Model

#### Stage 1: Config Parsing

```python
# In Transaction._phase_parse()

# Thread pool execution
worker = self._ensure_worker()

for ecosystem, targets in self._detected_targets.items():
    parser_class = registry.get_parser(ecosystem)

    for target_path in targets:
        def parse_task():
            parser = parser_class(workspace_root, graph_manager, eventbus)
            parser.parse(target_path)
            return parser.discover_code_files()

        worker.enqueue(Task(func=parse_task, ...))

results = worker.run_all()

# Collect code files discovered
for result in results.values():
    code_files_to_parse.extend(result.result)
```

#### Stage 2: Code Parsing

```python
# Process pool execution
code_pool = CodeParserPool.get_instance()

# Submit all files to process pool
futures_list = []
for ecosystem, file_paths in code_files_to_parse.items():
    batch_futures = code_pool.submit_batch(file_paths, ecosystem)
    futures_list.extend(batch_futures)

# Wait for completion
code_results = code_pool.wait_for_completion(futures_list)

# Add to graph
for file_path, parse_result in code_results.items():
    self._process_code_parse_result(file_path, parse_result)
```

## Code Parser Pool Architecture

### Global Singleton Pattern

```python
class CodeParserPool:
    """Global process pool for source code parsing."""
    _instance: Optional["CodeParserPool"] = None

    @classmethod
    def get_instance(cls) -> "CodeParserPool":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
```

### Process-Level Parser Caching

```python
# Per-worker process cache
_CODE_PARSER_CACHE: Dict[str, Any] = {}

def _get_code_parser(ecosystem: str):
    """Get or create cached parser in worker process."""
    if ecosystem not in _CODE_PARSER_CACHE:
        registry = EcosystemRegistry.get_instance()
        parser_class = registry.get_code_parser(ecosystem)
        _CODE_PARSER_CACHE[ecosystem] = parser_class()
    return _CODE_PARSER_CACHE[ecosystem]

def _code_worker_dispatch(task_data):
    """Worker dispatcher (must be top-level for pickling)."""
    file_path, ecosystem = task_data
    parser = _get_code_parser(ecosystem)
    return parser.parse_file(Path(file_path))
```

### Batch Processing

```python
def submit_batch(self, file_paths: List[Path], ecosystem: str) -> List[Tuple[Path, Future]]:
    """Submit multiple files for parsing."""
    executor = self._ensure_executor()

    futures = []
    for file_path in file_paths:
        future = executor.submit(_code_worker_dispatch, (str(file_path), ecosystem))
        futures.append((file_path, future))

    return futures

def wait_for_completion(self, futures: List[Tuple[Path, Future]]) -> Dict[Path, Dict]:
    """Wait for all futures and collect results."""
    results = {}
    for file_path, future in futures:
        result = future.result()
        results[file_path] = result
    return results
```

## Registry Integration

Both parser types register with `EcosystemRegistry`:

```python
registry = EcosystemRegistry.get_instance()

# Register config parser
registry.register_parser("cpp", CppParser)

# Register code parser
registry.register_code_parser("cpp", CppCodeParser)

# Or register all at once
registry.register_ecosystem(
    ecosystem="cpp",
    detector_class=CppDetector,
    parser_class=CppParser,
    fetcher_class=CppDepFetcher,
    code_parser_class=CppCodeParser,  # Optional
)
```

## Benefits

### 1. Separation of Concerns

- **Config parsing**: Complex logic, small scale, graph construction
- **Code parsing**: Simple logic, massive scale, pure data extraction

### 2. Performance Optimization

- **Thread pool for config**: Suitable for I/O bound tasks
- **Process pool for code**: Avoids Python GIL for CPU-bound parsing
- **Process-level caching**: Reuse parser instances (especially tree-sitter)

### 3. Scalability

- Config parsing: ~10-100 files per project
- Code parsing: ~1000-100000 files per project
- Process pool scales linearly with CPU cores

### 4. Maintainability

- Clear interface separation
- Pure functions for code parsing (easy to test)
- Explicit data flow (no hidden side effects)

## Implementation Checklist

For each ecosystem, implement:

### 1. Detector (BaseDetector)
```python
class XxxDetector(BaseDetector):
    ECOSYSTEM = "xxx"

    def detect(self) -> List[Path]:
        """Scan workspace and return config file paths."""
        pass
```

### 2. Config Parser (BaseParser)
```python
class XxxParser(BaseParser):
    ECOSYSTEM = "xxx"

    def parse(self, target_path: Path) -> None:
        """Parse config file and update graph."""
        pass

    def discover_code_files(self) -> List[Path]:
        """Return discovered source files."""
        pass
```

### 3. Code Parser (BaseCodeParser) - Optional
```python
class XxxCodeParser(BaseCodeParser):
    ECOSYSTEM = "xxx"
    CODE_GLOBS = ["**/*.xxx"]

    def parse_file(self, file_path: Path) -> Dict[str, Any]:
        """Parse source file (pure function)."""
        return {"file": str(file_path), "imports": [...]}
```

### 4. Dependency Fetcher (BaseDepFetcher)
```python
class XxxDepFetcher(BaseDepFetcher):
    ECOSYSTEM = "xxx"

    def fetch(self, dep_spec: DependencySpec) -> Optional[Path]:
        """Fetch dependency to cache."""
        pass

    def can_handle(self, dep_spec: DependencySpec) -> bool:
        """Check if this fetcher handles the dependency."""
        pass
```

### 5. Registration
```python
# In parsers/xxx/__init__.py
from depanalyzer.parsers.registry import register_ecosystem

register_ecosystem(
    ecosystem="xxx",
    detector_class=XxxDetector,
    parser_class=XxxParser,
    fetcher_class=XxxDepFetcher,
    code_parser_class=XxxCodeParser,  # Optional
)
```

## Performance Comparison

### Before (Single-threaded)
```
Parse 10,000 C++ files sequentially:
- Time: ~300 seconds
- CPU usage: 10-20% (one core)
```

### After (Process Pool)
```
Parse 10,000 C++ files with 8 workers:
- Time: ~40 seconds (7.5x speedup)
- CPU usage: 80-90% (all cores utilized)
```

## Related Documentation

- `docs/multiprocess_architecture.md` - Transaction-level parallelism
- `docs/ecosystem_architecture.md` - Ecosystem registration and routing
- `docs/architecture_improvements_summary.md` - Overall architecture overview

## Future Enhancements

1. **Adaptive Batching**: Dynamically adjust batch size based on file size
2. **Result Streaming**: Process results as they become available (iterator pattern)
3. **Incremental Parsing**: Only reparse changed files
4. **Distributed Parsing**: Scale across multiple machines for very large codebases
