"""Shared helpers for stable graph and node identifiers across exports."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def _sanitize_fragment(raw: Any) -> str:
    """Sanitize a fragment so it can safely appear inside node IDs."""
    if raw is None:
        return ""

    text = str(raw).strip()
    if not text:
        return ""

    cleaned = (
        text.replace("\\", "/")
        .replace(":", "_")
        .replace("/", "_")
        .replace(" ", "_")
    )
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_")


def _split_root(root_path: str | None) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Best-effort decomposition of a dependency root path.

    Returns:
        Tuple of (scope, package, version)
    """
    if not root_path:
        return None, None, None

    try:
        parts = Path(root_path).parts
    except Exception:
        return None, None, None

    scope = None
    package = None
    version = None

    if "hvigor" in parts:
        scope = "ohpm"
        try:
            hv_idx = parts.index("hvigor")
            if hv_idx + 1 < len(parts):
                package = parts[hv_idx + 1]
            if hv_idx + 2 < len(parts):
                version = parts[hv_idx + 2]
        except ValueError:
            pass

    if scope is None:
        scope = "workspace"
        if parts:
            package = parts[-1]

    return scope, package, version


def derive_dependency_namespace(
    graph_id: str, metadata: Dict[str, Any], summary: Optional[Dict[str, Any]] = None
) -> str:
    """Derive a stable namespace for dependency graphs.

    Namespace format:
        <scope>/<origin>

    - scope: workspace / ohpm / sys / <ecosystem>
    - origin: logical name (optionally with @version)
    """
    meta = metadata or {}
    info = summary or {}

    name = meta.get("name") or info.get("name")
    version = meta.get("version") or info.get("version") or meta.get("path_namespace")
    scope, package_from_root, version_from_root = _split_root(
        str(meta.get("root_path") or info.get("source") or "")
    )

    if not version and version_from_root:
        version = version_from_root

    origin = name or package_from_root
    if not origin and meta.get("path_namespace"):
        origin = meta["path_namespace"]

    if origin and scope == "ohpm":
        origin = origin.replace("_", ".")
    if origin and version:
        origin = f"{origin}@{version}"

    cleaned_scope = _sanitize_fragment(scope)
    cleaned_origin = _sanitize_fragment(origin)

    if cleaned_scope and cleaned_origin:
        return f"{cleaned_scope}/{cleaned_origin}"

    if cleaned_origin:
        return cleaned_origin

    if cleaned_scope:
        return cleaned_scope

    digest = hashlib.sha256(graph_id.encode("utf-8")).hexdigest()[:12]
    return f"anon/{digest}"


def ensure_namespace_unique(
    namespace: str, graph_id: str, assigned: Dict[str, str]
) -> str:
    """Ensure namespaces remain unique across merged graphs.

    Args:
        namespace: Proposed namespace derived from metadata.
        graph_id: Registry graph identifier.
        assigned: Mutable mapping of graph_id->namespace already chosen.

    Returns:
        Stable namespace that does not collide with prior assignments.
    """
    if graph_id in assigned:
        return assigned[graph_id]

    if namespace not in assigned.values():
        assigned[graph_id] = namespace
        return namespace

    suffix = hashlib.sha256(graph_id.encode("utf-8")).hexdigest()[:6]
    candidate = f"{namespace}-{suffix}"

    while candidate in assigned.values():
        suffix = hashlib.sha256((graph_id + suffix).encode("utf-8")).hexdigest()[:6]
        candidate = f"{namespace}-{suffix}"

    assigned[graph_id] = candidate
    return candidate


def apply_namespace_prefix(
    node_id: str, namespace: Optional[str], is_dependency: bool
) -> str:
    """Prefix node ID for dependency exports so scan/scancode stay aligned.

    Args:
        node_id: Canonical node identifier (e.g. //path:leaf).
        namespace: Derived namespace for the dependency graph.
        is_dependency: Whether the node belongs to a dependency graph.

    Returns:
        Node identifier adjusted for merged export semantics.
    """
    if not is_dependency or not namespace:
        return node_id

    canonical = node_id if str(node_id).startswith("//") else f"//{str(node_id).lstrip('/')}"
    body = canonical.lstrip("/")
    body = body.replace(":", "/")

    ns = namespace.strip("/ ")
    if not ns:
        ns = "dep"

    return f"//dep/{ns}/{body}"
