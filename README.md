# Depanalyzer

Depanalyzer is a powerful dependency analysis tool designed to parse, link, and visualize dependencies across multiple languages and build systems. It supports static analysis of mixed-language projects, third-party dependency resolution, and automated license compliance checking.

## 🚀 Features

*   **Multi-Language Support:** C/C++ (CMake), TypeScript/JavaScript (Hvigor), Java (Maven).
*   **Deep Dependency Resolution:** Recursively fetches and analyzes third-party dependencies.
*   **Graph-Based Architecture:** Represents projects as a directed acyclic graph (DAG) of file nodes and edge relationships.
*   **License Compliance:** Integrated support for [ScanCode Toolkit](https://github.com/aboutcode-org/scancode-toolkit) and **Liscopelens** to detect licenses and verify compatibility.
*   **High Performance:** Multiprocess architecture for parallel parsing and analysis.
*   **Automated Pipeline:** One-click script to run scanning, license detection, and compatibility checks.

## 📦 Installation

### Prerequisites
*   Python 3.12+
*   Git (for fetching dependencies)

### 1. Using Pip (Recommended)

```bash
pip install .
```

### 2. Setting up License Checks (Optional but Recommended)

To use the license scanning and compatibility features, you need two additional components:

1.  **ScanCode Toolkit:**
    You can install it system-wide, or let Depanalyzer manage a local copy for you:
    ```bash
    # Download and configure a local copy of ScanCode (recommended)
    depanalyzer --install
    ```

2.  **Liscopelens (for Compatibility Checks):**
    Required if you want to run the full compliance pipeline.
    ```bash
    pip install liscopelens
    ```

### 3. Using Docker (All-in-One)

The Docker image comes pre-configured with Depanalyzer, ScanCode, and Liscopelens. This is the easiest way to run the full compliance pipeline.

```bash
# Build the image
docker build -t depanalyzer .
```

## 🛠️ Usage

### 1. Automated License Compliance Pipeline

The project includes a robust script (`scripts/run_license_compatibility.py`) that automates the entire workflow: **Scan -> Detect Licenses -> Check Compatibility**.

**Running via Docker (Recommended):**

The Docker image defaults to running this pipeline.

```bash
# Analyze a single project
docker run --rm \
  -v /path/to/local/repo:/workspace/project \
  -v $(pwd)/output:/workspace/output \
  depanalyzer --project-path /workspace/project

# Analyze with third-party dependencies (recursive)
docker run --rm \
  -v /path/to/local/repo:/workspace/project \
  -v $(pwd)/output:/workspace/output \
  depanalyzer --project-path /workspace/project --third-party
```

**Running Locally:**

Ensure you have `liscopelens` installed and `scancode` available (or installed via `depanalyzer --install`).

```bash
# Run the pipeline script
python scripts/run_license_compatibility.py \
  --project-path /path/to/repo \
  --output-dir ./results \
  --third-party
```

**Batch Processing:**

You can analyze multiple projects in one go. This is supported in both Docker and local modes.

*Docker mode:*
```bash
# Create a list of projects
echo "/workspace/project1" > projects_list.txt
echo "/workspace/project2" >> projects_list.txt

# Run batch analysis in Docker
docker run --rm \
  -v /path/to/p1:/workspace/project1 \
  -v /path/to/p2:/workspace/project2 \
  -v $(pwd)/projects_list.txt:/workspace/projects.txt \
  -v $(pwd)/output:/workspace/output \
  depanalyzer --projects-file /workspace/projects.txt
```

*Local mode:*
```bash
# Option 1: Using a projects file (recommended for many projects)
cat > projects_list.txt << EOF
/path/to/project1
/path/to/project2
/path/to/project3
EOF

python scripts/run_license_compatibility.py \
  --projects-file projects_list.txt \
  --output-dir ./results \
  --third-party

# Option 2: Using multiple --project-path flags
python scripts/run_license_compatibility.py \
  --project-path /path/to/project1 \
  --project-path /path/to/project2 \
  --project-path /path/to/project3 \
  --output-dir ./results \
  --third-party
```

When running in batch mode, each project gets its own subdirectory (e.g., `01_project1/`, `02_project2/`), and a `batch_summary.json` file is generated at the root of the output directory with results for all projects.

### 2. Manual Dependency Scanning (`scan`)

If you only need the dependency graph without license checks:

```bash
# Basic scan
depanalyzer scan /path/to/repo -o graph.json

# Scan with third-party dependency resolution (depth 3)
depanalyzer scan /path/to/repo -o graph.json --third-party --max-depth 3
```

### 3. Manual License Scanning (`scancode`)

Generate a license map (`{node_id: license_expression}`) manually. This requires `scancode` to be installed.

```bash
# 1. Scan directory directly (fastest, no dependency graph needed)
depanalyzer scancode --path /path/to/repo -o license_map.json

# 2. Scan using a cached graph (enables analysis of third-party dependencies)
# First, run a scan:
depanalyzer scan /path/to/repo -o graph.json --third-party
# Then, run scancode using the generated cache:
depanalyzer scancode --source /path/to/repo --third-party -o license_map.json
```

### 4. Other Commands

*   **Export:** Convert graphs to other formats (GML, DOT).
    ```bash
    depanalyzer export <graph_id> -o graph.gml --format gml
    ```
*   **DAG Validation:** Check for circular dependencies in the global package graph.
    ```bash
    depanalyzer dag --fail-on-cycle
    ```

## 📁 Output Structure

### Pipeline Output
When running the pipeline (Docker or script), the output directory will contain:

*Single project:*
```text
output/
├── graph.json                  # Dependency graph
├── license_map.json            # Raw license findings
├── compatibility_results.json  # Compliance check results
└── compatibility_graph.json    # Visualizable compliance graph
```

*Batch mode (multiple projects):*
```text
output/
├── 01_project_name1/
│   ├── graph.json                  # Dependency graph
│   ├── license_map.json            # Raw license findings
│   ├── compatibility_results.json  # Compliance check results
│   └── compatibility_graph.json    # Visualizable compliance graph
├── 02_project_name2/
│   ├── graph.json
│   ├── license_map.json
│   ├── compatibility_results.json
│   └── compatibility_graph.json
└── batch_summary.json              # Summary of all processed projects
```

### Graph JSON Format
The `graph.json` contains:
*   **Nodes**: Files, packages, or targets with `id`, `type`, and `data`.
*   **Edges**: Relationships like `import`, `link`, `includes`.
*   **Metadata**: Scan configuration and source details.

## 🔧 Configuration

You can provide a custom configuration file for the scan process using the `--config` flag.

```bash
depanalyzer scan . -o graph.json --config config.toml
```

### Fallback Policy

The fallback policy creates a synthetic tree that connects unparsed files and isolated nodes to a root node. This ensures license scanning can achieve full coverage even when some files couldn't be parsed.

**Enable via CLI flag:**
```bash
depanalyzer scan /path/to/repo -o graph.json --fallback-tree
```

**Enable via configuration file:**

`config.toml`:
```toml
[fallback]
enabled = true                           # Enable fallback tree (default: false)
root_id = "fallback:license_scan"       # Root node ID (default: "fallback:license_scan")
include_isolated_nodes = true            # Connect isolated nodes to root (default: true)
```

`config.json`:
```json
{
  "fallback": {
    "enabled": true,
    "root_id": "fallback:license_scan",
    "include_isolated_nodes": true
  }
}
```

**When to use:** Enable this when running license compliance checks on projects with incomplete parsing or mixed-language codebases to ensure all files are included in the analysis.

### Other Configuration Options

For advanced configuration options (projection, contract matching, per-ecosystem settings), see `docs/dependency_graph_lifecycle_and_config.md`
