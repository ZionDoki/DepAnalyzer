"""Schema normalization helpers for graph export.

This module centralizes node and edge schema defaults and provides
utilities to convert internal node identifiers into canonical //-prefixed
IDs and labels for export (GN-inspired but not strict GN target strings).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from depanalyzer.utils.path_utils import normalize_node_id


def canonicalize_normalized_id(normalized_id: str) -> str:
    """Convert a normalized node ID into canonical export form with slash separators.

    Args:
        normalized_id: Normalized node ID (//... or path-like string).

    Returns:
        Canonical identifier using leading '//' and '/' separators.
    """
    value = str(normalized_id).strip()
    if not value:
        return value

    body = value[2:] if value.startswith("//") else value.lstrip("/")
    body = body.replace("\\", "/")

    special_prefixes = ("system:", "external:", "include:", "proxy:")
    if any(body.startswith(prefix) for prefix in special_prefixes):
        return "//" + body

    def _split_segment(segment: str) -> list[str]:
        # Keep segments with explicit scopes (e.g., @ohos/pkg) intact.
        if segment.startswith("@"):
            return [segment]
        if ":" in segment:
            return [part for part in segment.split(":") if part]
        return [segment]

    parts: list[str] = []
    for seg in body.split("/"):
        parts.extend(_split_segment(seg))

    return "//" + "/".join(part for part in parts if part)


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
    src_path = attrs.get("src_path")
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


def canonicalize_node(
    raw_id: str,
    attrs: Dict[str, Any],
    root_path: Optional[Path],
) -> Tuple[str, Dict[str, Any]]:
    """Apply schema defaults and canonical //-prefixed identity to a node.

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

    # Derive canonical identifier.
    #
    # There are two broad categories of IDs:
    # 1) Path-based IDs, which should be normalized relative to root_path
    #    (e.g. file/config/module paths).
    # 2) Logical IDs that are not filesystem paths, such as external library
    #    identifiers (ext_lib:pkg@version). These must NOT be treated as paths
    #    or they will accidentally pick up process working directories.
    if raw_id.startswith("ext_lib:"):
        # External library nodes are logical identifiers; normalize separators
        # so they are stable across platforms and readable.
        lib_body = raw_id.split("ext_lib:", 1)[1]
        lib_body = lib_body.replace("/", ".")
        canonical_id = f"//ext_lib/{lib_body}"
    elif raw_id.startswith("module:"):
        # Modules behave like logical identifiers; normalize to a path-like form.
        mod_body = raw_id.split("module:", 1)[1].replace(":", "/")
        canonical_id = f"//module/{mod_body}"
    else:
        # Default: best-effort path normalization where applicable.
        normalized = _derive_normalized_path(raw_id, new_attrs, root_path)
        if normalized:
            canonical_id = canonicalize_normalized_id(normalized)
        else:
            canonical_id = raw_id

    # If still not a path-like canonical id, coerce obvious path/drive/id forms.
    if not str(canonical_id).startswith("//"):
        if not str(canonical_id).startswith(
            (
                "ext_lib:",
                "module:",
                "dep_graph:",
            )
        ):
            if any(ch in str(canonical_id) for ch in ("/", "\\", ":")):
                coerced = canonicalize_normalized_id(
                    "//" + str(canonical_id).lstrip("/\\")
                )
                canonical_id = coerced

    # Always derive label from canonical ID so id/label stay in sync in exports.
    new_attrs["label"] = canonical_id

    # Best-effort name default for file-like nodes
    if "name" not in new_attrs or not new_attrs["name"]:
        src = new_attrs.get("src_path")
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
