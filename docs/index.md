# Depanalyzer Documentation

Depanalyzer is a multi-language dependency analysis tool that parses, links, and visualizes dependencies across C/C++, JavaScript/TypeScript, and Java projects. It supports deep third-party dependency resolution and automated license compliance checking.

## Documentation Overview

### For Users

- **Getting Started** — Install and run your first scan. ([details](getting-started.md))
- **Usage Guide** — Cache layout, merged exports, and CLI behavior. ([details](usage.md))
- **CLI Reference** — Full flag/argument reference for every command. ([details](cli-reference.md))
- **Configuration** — GraphBuildConfig and environment variables. ([details](configuration.md))
- **Scripts** — Pipeline and explorer scripts (all options). ([details](scripts.md))

### For Developers

- **Architecture** — Design philosophy, lifecycle pipeline, and core components. ([details](architecture.md))
- **Extension Guide** — Add new ecosystems and runtime extensions. ([details](extension-guide.md))

## Quick Links

### For Users

- **New to Depanalyzer?** Start with [Getting Started](getting-started.md)
- **Want behavior details?** Read the [Usage Guide](usage.md)
- **Need command help?** See [CLI Reference](cli-reference.md)
- **Configuring a scan?** Check [Configuration](configuration.md)
- **Running license checks?** Read [Scripts](scripts.md)

### For Developers

- **Understanding the system?** Read [Architecture](architecture.md)
- **Adding a new ecosystem?** Follow [Extension Guide](extension-guide.md)

## Supported Ecosystems

| Ecosystem | Build System | Languages |
|-----------|--------------|-----------|
| C/C++ | CMake | C, C++ |
| npm | package.json | JavaScript, TypeScript |
| Hvigor | build-profile.json5 | ArkTS (OpenHarmony) |
| Maven | pom.xml | Java |

## Key Features

- **Multi-language analysis** - Parse dependencies across different build systems
- **Third-party resolution** - Recursively fetch and analyze external dependencies
- **License compliance** - Integrated ScanCode support for license detection
- **Export & views** - Export JSON graphs and derived views (e.g., asset→artifact mapping)
- **Interactive explorer** - Terminal UI for dependency tracing
- **Extensible architecture** - Plugin system for adding new ecosystems
