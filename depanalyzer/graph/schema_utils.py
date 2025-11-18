"""Schema normalization helpers for graph export.

This module centralizes node and edge schema defaults and provides
utilities to convert internal node identifiers into canonical GN-style
IDs and labels for export.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from depanalyzer.utils.path_utils import normalize_node_id


def _derive_normalized_path(
    raw_id: str,
    attrs: Dict[str, Any],
    root_path: Optional[Path],
) -> Optional[str]:
    """Derive a normalized path-based node ID from attributes.

    Preference order:
    1) src_path attribute (absolute path)
    2) path attribute (absolute path)
    3) Existing raw_id if it already looks like a normalized ID (starts with //)
    4) raw_id interpreted as a filesystem path relative to root_path

    Args:
        raw_id: Original node identifier used in the in-memory graph.
        attrs: Node attribute mapping.
        root_path: Optional graph root path.

    Returns:
        Optional[str]: Normalized ID (//...) or None if it cannot be derived.
    """
    src_path = attrs.get("src_path") or attrs.get("path")
    if isinstance(src_path, str) and src_path:
        try:
            return normalize_node_id(Path(src_path), root_path)
        except (OSError, ValueError):
            return None

    if raw_id.startswith("//"):
        return raw_id

    # Only treat raw_id as a filesystem path if it clearly looks like one
    if root_path is not None and ("/" in raw_id or "\\" in raw_id):
        try:
            return normalize_node_id(Path(raw_id), root_path)
        except (OSError, ValueError):
            return None

    return None


def _gn_style_id(normalized_id: str) -> str:
    """Convert a normalized path ID into GN-style `//path/to:target` form.

    Special IDs like `//system:*`, `//external:*`, `//include:*`, `//proxy:*`
    are returned unchanged.

    Args:
        normalized_id: Normalized node ID (//...).

    Returns:
        GN-style identifier string.
    """
    if not normalized_id.startswith("//"):
        return normalized_id

    body = normalized_id[2:]

    # Special namespaces are already canonical
    special_prefixes = ("system:", "external:", "include:", "proxy:")
    if any(body.startswith(prefix) for prefix in special_prefixes):
        return normalized_id

    if ":" in body:
        return normalized_id

    if "/" not in body:
        return normalized_id

    dir_part, leaf = body.rsplit("/", 1)
    return f"//{dir_part}:{leaf}"


def canonicalize_node(
    raw_id: str,
    attrs: Dict[str, Any],
    root_path: Optional[Path],
) -> Tuple[str, Dict[str, Any]]:
    """Apply schema defaults and GN-style identity to a node.

    Args:
        raw_id: Original node identifier.
        attrs: Node attribute mapping.
        root_path: Optional graph root path for relative path computation.

    Returns:
        Tuple[str, Dict[str, Any]]: (canonical_id, updated_attributes).
    """
    new_attrs: Dict[str, Any] = dict(attrs) if attrs is not None else {}

    # Core schema defaults
    new_attrs.setdefault("type", "unknown")
    new_attrs.setdefault("confidence", 1.0)
    new_attrs.setdefault("over_approx", False)
    new_attrs.setdefault("uncertainty_category", "definite")
    new_attrs.setdefault("uncertainty_reasons", [])

    # Normalize path fields: prefer src_path, mirror legacy 'path' into src_path
    if "src_path" not in new_attrs and isinstance(new_attrs.get("path"), str):
        new_attrs["src_path"] = new_attrs["path"]

    # Derive canonical identifier.
    #
    # There are two broad categories of IDs:
    # 1) Path-based IDs, which should be normalized relative to root_path
    #    (e.g. file/config/module paths).
    # 2) Logical IDs that are not filesystem paths, such as external library
    #    identifiers (ext_lib:pkg@version). These must NOT be treated as paths
    #    or they will accidentally pick up process working directories.
    if raw_id.startswith("ext_lib:"):
        # External library nodes are modeled as logical identifiers. For export
        # purposes we wrap them in a //-style prefix so they live in the same
        # namespace as other node IDs, but we avoid any filesystem-based
        # normalization.
        canonical_id = f"//{raw_id}"
    else:
        # Default: best-effort path normalization where applicable.
        normalized = _derive_normalized_path(raw_id, new_attrs, root_path)
        if normalized:
            canonical_id = _gn_style_id(normalized)
        else:
            canonical_id = raw_id

    # Always derive label from canonical ID so id/label stay in sync in exports.
    new_attrs["label"] = canonical_id

    # Best-effort name default for file-like nodes
    if "name" not in new_attrs or not new_attrs["name"]:
        src = new_attrs.get("src_path") or new_attrs.get("path")
        if isinstance(src, str) and src:
            new_attrs["name"] = Path(src).name

    return canonical_id, new_attrs


def canonicalize_edge(attrs: Dict[str, Any]) -> Dict[str, Any]:
    """Apply schema defaults to an edge attribute mapping.

    Args:
        attrs: Edge attribute mapping.

    Returns:
        Dict[str, Any]: Updated edge attributes with defaults applied.
    """
    new_attrs: Dict[str, Any] = dict(attrs) if attrs is not None else {}

    new_attrs.setdefault("kind", "unknown")
    new_attrs.setdefault("confidence", 1.0)
    new_attrs.setdefault("over_approx", False)
    new_attrs.setdefault("uncertainty_category", "definite")
    new_attrs.setdefault("uncertainty_reasons", [])

    if "label" not in new_attrs or not new_attrs["label"]:
        new_attrs["label"] = new_attrs.get("kind", "unknown")

    return new_attrs
