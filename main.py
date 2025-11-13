#!/usr/bin/env python3
"""Depanalyzer - Dependency Analysis Tool

A comprehensive dependency analysis tool for mixed Hvigor+CMake projects
following a single-transaction, unified-graph, detect→parse→join architecture.

Usage:
    depanalyzer scan <source> -o <output> [options]
    depanalyzer export <input> -o <output> [options]
    depanalyzer query <graph> [options]
    depanalyzer explain <graph> [options]

Architecture follows AGENTS.md design:
- runtime/: Transaction, workspace, worker, eventbus, lifecycle
- graph/: Manager, backend, registry, global_dag, proxy
- parsers/: Detector + parser pattern (hvigor, cmake, prebuilt, deps)
- hooks/: Cross-domain associations (hvigor_cmake_bridge, packaging_bridge, etc.)
- analysis/: Linkage analysis, deadcode detection
- export/: JSON, GraphML, DOT exporters

For backward compatibility, the old CLI interface is still available via app.run.
"""

import sys

# Use new CLI by default
from cli.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
