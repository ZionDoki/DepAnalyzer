# Getting Started

This guide helps you install Depanalyzer and run your first dependency scan.

## Prerequisites

- **Python 3.12+** - Required for running Depanalyzer
- **Git** - Required for fetching third-party dependencies

## Installation

### Option 1: Install from Source (Recommended)

```bash
# Clone the repository
git clone https://github.com/your-org/depanalyzer.git
cd depanalyzer

# Install with pip
pip install .

# Or install in development mode
pip install -e .
```

### Option 2: Using Poetry

```bash
# Install dependencies
poetry install

# Run via poetry
poetry run depanalyzer --help
```

### Option 3: Using Docker

```bash
# Build the Docker image
docker build -t depanalyzer .

# Run the built-in license/compliance pipeline (default entrypoint)
docker run --rm \
  -v /path/to/project:/workspace/project \
  -v "$(pwd)/output":/workspace/output \
  depanalyzer --project-path /workspace/project
```

## Your First Scan

### Basic Dependency Scan

Scan a local project and output the dependency graph:

```bash
depanalyzer scan /path/to/your/project -o graph.json
```

This produces a `graph.json` file containing:
- **nodes**: Files, modules, and targets in your project
- **edges**: Dependency relationships between nodes
- **metadata**: Scan configuration and statistics

### Understanding the Output

The output JSON has this structure:

```json
{
  "nodes": [
    {
      "id": "//src/main.cpp",
      "type": "code",
      "label": "main.cpp",
      "src_path": "/abs/path/to/src/main.cpp"
    }
  ],
  "edges": [
    {
      "source": "//src/main.cpp",
      "target": "//include/utils.h",
      "kind": "include"
    }
  ],
  "metadata": {
    "graph_id": "abc123",
    "source": "/path/to/your/project",
    "node_count": 42,
    "edge_count": 87
  }
}
```

### Scan with Third-Party Dependencies

To recursively analyze external dependencies:

```bash
depanalyzer scan /path/to/project -o graph.json --third-party --max-depth 3
```

Options:
- `--third-party` or `-t`: Enable third-party dependency resolution
- `--max-depth`: Maximum recursion depth (default: 3)

### Export to Other Formats

Export cached graphs to JSON views:

```bash
# Export a JSON bundle from the scan cache (include dependency graphs if needed)
depanalyzer export <graph_id> -o export.json --with-deps --work-dir .dep_cache/<source_stem>

# Export asset→artifact mapping view
depanalyzer export <graph_id> -o asset_artifact.json --format asset_artifact --work-dir .dep_cache/<source_stem>
```

## Setting Up License Scanning

For license compliance checking, install ScanCode:

```bash
# Let Depanalyzer install ScanCode automatically
depanalyzer --install

# Or specify a version
depanalyzer --install 32.4.1
```

Then run license detection:

```bash
# Scan a directory for licenses
depanalyzer scancode --path /path/to/project -o license_map.json
```

## Common Workflows

### 1. Quick Dependency Overview

```bash
depanalyzer scan . -o graph.json
```

### 2. Full Project Analysis with Dependencies

```bash
depanalyzer scan . -o graph.json -t -d 3 -w 8
```

Options:
- `-t`: Enable third-party deps
- `-d 3`: Max depth of 3
- `-w 8`: Use 8 worker threads

### 3. License Compliance Pipeline

```bash
# Run the full pipeline
python scripts/run_license_compatibility.py \
  --project-path /path/to/project \
  --output-dir ./results \
  --third-party
```

### 4. Interactive Graph Exploration

```bash
python scripts/graph_explorer_tui.py graph.json
```

## Next Steps

- **[Usage Guide](usage.md)** - CLI behavior, cache layout, merged exports
- **[CLI Reference](cli-reference.md)** - Full flag/argument reference
- **[Configuration](configuration.md)** - Customize scan behavior
- **[Scripts](scripts.md)** - Automation and batch processing
- **[Architecture](architecture.md)** - Understand the system design

## Troubleshooting

### "depanalyzer: command not found"

Ensure the package is installed and your PATH includes Python's bin directory:

```bash
pip install -e .
# Or run directly
python -m depanalyzer.main scan ...
```

### Slow scans on large projects

Increase worker count and consider disabling analysis:

```bash
depanalyzer scan . -o graph.json -w 16 --no-analyze
```

### Missing dependencies in graph

Enable third-party resolution:

```bash
depanalyzer scan . -o graph.json --third-party
```

### ScanCode not found

Install ScanCode or set the environment variable:

```bash
# Auto-install
depanalyzer --install

# Or point to existing installation
export SCANCODE_BIN=/path/to/scancode
```
