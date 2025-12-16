# Depanalyzer

Depanalyzer is a powerful dependency analysis tool designed to parse, link, and visualize dependencies across multiple languages and build systems. It supports static analysis of mixed-language projects, third-party dependency resolution, and automated license compliance checking.

## 🚀 Features

*   **Multi-Language Support:** C/C++ (CMake), TypeScript/JavaScript (npm, Hvigor), Java (Maven).
*   **Deep Dependency Resolution:** Recursively fetches and analyzes third-party dependencies.
*   **Graph-Based Architecture:** Represents projects as a typed directed multigraph; exports are DAG-safe via cycle condensation.
*   **License Compliance:** Integrated support for [ScanCode Toolkit](https://github.com/aboutcode-org/scancode-toolkit) and **Liscopelens** to detect licenses and verify compatibility.
*   **High Performance:** Multiprocess architecture for parallel parsing and analysis.
*   **Automated Pipeline:** One-click script to run scanning, license detection, and compatibility checks.
*   **Interactive TUI:** Terminal-based graph explorer to visualize and trace dependencies.

## 📚 Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/architecture.md) | System design, 7-phase lifecycle, and core components |
| [Getting Started](docs/getting-started.md) | Quick installation and first scan guide |
| [Usage Guide](docs/usage.md) | Cache layout, merged exports, and CLI behavior |
| [CLI Reference](docs/cli-reference.md) | Complete command-line interface documentation |
| [Configuration](docs/configuration.md) | All configuration options (TOML/JSON/environment variables) |
| [Scripts](docs/scripts.md) | Helper scripts for license compliance and graph exploration |
| [Extension Guide](docs/extension-guide.md) | How to add new ecosystems, analyzers, and hooks |

For the full documentation index, see [docs/index.md](docs/index.md).

## 📦 Installation

### Prerequisites
*   Python 3.12+
*   Git (for fetching dependencies)

### 1. Using Pip (Recommended)

```bash
pip install depanalyzer
```

or install from source code:

```bash
# clone the repository, cd respository root path
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
    **Manual ScanCode installation**

    Depanalyzer only needs access to the `scancode` executable. You can either place it on your `PATH` (system command mode) or keep it in a project-specific directory and point Depanalyzer at it (non-system command mode).

    **System command mode (global `scancode`):**

    1. Download the latest release archive from the [ScanCode Toolkit releases page](https://github.com/aboutcode-org/scancode-toolkit/releases).
    2. Extract it somewhere such as `/opt/scancode-toolkit` (Linux/macOS) or `C:\tools\scancode-toolkit` (Windows) and run the bundled configure script once:
        ```bash
        cd /opt/scancode-toolkit-<version>
        ./scancode --help   # on Windows use .\scancode.bat --help
        ```
    3. Add the directory that contains the `scancode` (or `scancode.bat`) executable to your shell path:
        ```bash
        # Linux/macOS
        export PATH="/opt/scancode-toolkit-<version>:$PATH"

        # PowerShell profile (Windows)
        $env:Path = "C:\\tools\\scancode-toolkit-<version>;" + $env:Path
        ```
    4. Verify the installation with `scancode --version`. Depanalyzer will automatically discover it via `PATH`.

    **Non-system command mode (local copy):**

    1. Download and extract the toolkit under your workspace, for example `tools/scancode-toolkit`.
    2. Run the configure script once (`./scancode --help`) so the bundled virtual environment is prepared.
    3. Point Depanalyzer at the executable by setting `SCANCODE_BIN` (or `SCANCODE_CLI`) to the full path of the `scancode` script before invoking the CLI or scripts:
        ```bash
        export SCANCODE_BIN="$PWD/tools/scancode-toolkit/scancode"        # Linux/macOS
        setx SCANCODE_BIN "C:\Workspace\project\tools\scancode-toolkit\scancode.bat"  # Windows
        ```
        `SCANCODE_BIN`/`SCANCODE_CLI` must reference the executable itself, while `SCANCODE_TOOLKIT` can point to the toolkit directory (matching the layout produced by `depanalyzer --install`, which defaults to `~/.depanalyzer/scancode-toolkit`). Depanalyzer checks these variables before falling back to `PATH`, so the command does not need to be globally installed. This mode does **not** expose `scancode` as a shell command; Depanalyzer calls it via the environment variable, and you can still run it manually by invoking the absolute path stored in `SCANCODE_BIN`.

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

If you maintain liscopelens `shadow.json` overrides, pass them explicitly:
```bash
# Single shadow applied to all projects
python scripts/run_license_compatibility.py \
  --project-path /path/to/repo \
  --shadow-file /path/to/shadow.json

# Batch with one shadow per project (order follows the project flags)
python scripts/run_license_compatibility.py \
  --project-path /path/to/project1 --shadow-file /shadows/project1/shadow.json \
  --project-path /path/to/project2 --shadow-file /shadows/project2/shadow.json

# Batch with a shadow directory (expects <shadow-dir>/<project-name>.json)
python scripts/run_license_compatibility.py \
  --projects-dir /path/to/projects_root \
  --shadow-dir /path/to/shadow_root
```
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

# Option 3: Point at a folder containing multiple projects (each direct subfolder is scanned)
python scripts/run_license_compatibility.py \
  --projects-dir /path/to/projects_root \
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

*   **Export:** Export cached graphs as JSON views (bundle export or derived views).
    ```bash
    # Export JSON bundle (include dependency graphs if present)
    depanalyzer export <graph_id> -o export.json --with-deps --work-dir .dep_cache/<source_stem>

    # Export asset→artifact mapping view
    depanalyzer export <graph_id> -o asset_artifact.json --format asset_artifact --work-dir .dep_cache/<source_stem>
    ```
*   **DAG Validation:** Check for circular dependencies in the global package graph.
    ```bash
    depanalyzer dag --fail-on-cycle --limit 50
    ```

### 5. Interactive Graph Explorer

A terminal-based UI (TUI) to visualize and trace dependencies interactively.

```bash
python scripts/graph_explorer_tui.py <path_to_graph.json>
```

Features:
*   Search nodes by ID or Label.
*   Trace paths between two nodes (Direct path and Common Ancestor).
*   View node details.

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
root_id = "//fallback/license_scan"       # Root node ID (default)
include_isolated_nodes = true            # Connect isolated nodes to root (default: true)
```

`config.json`:
```json
{
  "fallback": {
    "enabled": true,
    "root_id": "//fallback/license_scan",
    "include_isolated_nodes": true
  }
}
```

**When to use:** Enable this when running license compliance checks on projects with incomplete parsing or mixed-language codebases to ensure all files are included in the analysis.

### Other Configuration Options

For advanced configuration options (projection, contract matching, per-ecosystem settings), see [Configuration Reference](docs/configuration.md).
