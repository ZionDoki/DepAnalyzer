#!/usr/bin/env python3
"""Test license compatibility check."""

import json
import argparse
import re
from typing import Any, Dict, List

from liscopelens.api import check_compatibility


def _jsonify(obj: Any) -> Any:
    """Recursively convert sets and frozensets to sorted lists for JSON export.

    Args:
        obj: Arbitrary object tree to sanitize.

    Returns:
        JSON-serializable object.
    """
    if isinstance(obj, dict):
        return {k: _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, (set, frozenset)):
        return sorted(_jsonify(v) for v in obj)
    if isinstance(obj, list):
        return [_jsonify(v) for v in obj]
    return obj


DEFAULT_LISCOPLENS_CONFIG = {
    "blacklist": [],
    "license_isolations": [],
    "permissive_spreads": ["COMPILE", "STATIC_LINKING"],
    "edge_isolations": [],
    "edge_permissive_spreads": ["STATIC_LINKING"],
    "default_edge_behavior": "inherit",
    "edge_literal_mapping": {
        # Depanalyzer core edge kinds
        "depends_on": "DEPENDENCY",
        "import": "COMPILE",
        "sources": "COMPILE",
        "link_libraries": "STATIC_LINKING",
        "links": "STATIC_LINKING",
        "dependency": "DEPENDENCY",
        "include": "INCLUDE",
        "link": "STATIC_LINKING",
        "dynamic_link": "DYNAMIC_LINKING",
        "compile_depend": "COMPILE",
        "runtime_depend": "RUNTIME",
    },
    "literal_mapping": {
        "code": "COMPILE",
        "system_header": "COMPILE",
        "project_header": "COMPILE",
        "header": "COMPILE",
        "config": "COMPILE",
        "build_config": "GENERATED",
        "subdirectory": "COMPILE",
        "module": "COMPILE",
        "target": "COMPILE",
        "artifact": "COMPILE",
        "process": "COMPILE",
        "toolchain": "COMPILE",
        "proxy": "STATIC_LINKING",
        "external_library": "STATIC_LINKING",
        "external_dep": "STATIC_LINKING",
        "hap": "COMPILE",
        "hsp": "DYNAMIC_LINKING",
        "har": "STATIC_LINKING",
        "shared_library": "DYNAMIC_LINKING",
        "static_library": "STATIC_LINKING",
        "executable": "COMPILE",
        "asset": "GENERATED",
        "license": "COMPILE",
        "scc_cluster": "COMPILE",
        "code_scc_cluster": "COMPILE",
        "unknown": "COMPILE",
    },
}


shadow = {
    "//entry/src/main/ets/entryability/EntryAbility.ts": "GPL-3.0-only",
    "//NOTICE": "MIT",
}


def _normalize_spdx_expression(expr: str) -> str:
    """Coerce license expressions to SPDX-friendly case (best effort).

    Args:
        expr: Raw license expression from ScanCode output.

    Returns:
        Normalized SPDX-like expression.
    """
    tokens = re.findall(
        r"[A-Za-z0-9.\-+]+|\(|\)|AND|OR|WITH", expr, flags=re.IGNORECASE
    )
    normalized: List[str] = []
    for tok in tokens:
        upper = tok.upper()
        if tok in ("(", ")"):
            normalized.append(tok)
        elif upper in ("AND", "OR", "WITH"):
            normalized.append(upper)
        else:
            normalized.append(tok)
    return " ".join(normalized)


def load_license_map(license_map_path: str) -> Dict[str, str]:
    """Load aggregated node->license mapping from ScanCode output.

    Args:
        license_map_path: Path to the license map JSON file.

    Returns:
        Mapping of node identifiers to SPDX expressions.
    """
    with open(license_map_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"License map is not a mapping: {license_map_path}")
    return {str(k): str(v) for k, v in data.items()}


def main():
    parser = argparse.ArgumentParser(
        description="Test compatibility between two package versions."
    )

    parser.add_argument(
        "license_map",
        type=str,
        help="Path to the license map file.",
    )

    parser.add_argument(
        "graph_input",
        type=str,
        help="Path to the graph input file.",
    )

    args = parser.parse_args()

    # Load and normalize license map like run_license_compatibility.py
    license_map = {
        k: _normalize_spdx_expression(v)
        for k, v in load_license_map(args.license_map).items()
    }

    # Pass graph file path as string, not parsed JSON
    context, results = check_compatibility(
        license_map,
        args.graph_input,
        config=DEFAULT_LISCOPLENS_CONFIG,
        file_shadow=shadow,
        license_shadow=None,
        args={"ignore_unk": True, "merge_cycles": True},
    )

    with open("res.json", "w", encoding="utf-8") as f:
        json.dump(_jsonify(results), f, indent=4)

    context.save(file_path="./scripts/test.json")

if __name__ == "__main__":
    main()
