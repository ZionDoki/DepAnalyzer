"""Shared helpers for stable graph and node identifiers across exports."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Optional


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


def derive_dependency_namespace(
    graph_id: str, metadata: Dict[str, Any], summary: Optional[Dict[str, Any]] = None
) -> str:
    """Derive a human-meaningful namespace for dependency graphs.

    Args:
        graph_id: Registry graph identifier.
        metadata: Graph metadata loaded from the cached graph file.
        summary: Graph summary from the registry, if available.

    Returns:
        Namespace string suitable for prefixing dependency node IDs. Falls back
        to a stable hash of graph_id when no descriptive hints are present.
    """
    meta = metadata or {}
    info = summary or {}

    name = meta.get("name") or info.get("name")
    version = meta.get("version") or info.get("version")
    path_namespace = meta.get("path_namespace")

    combined = f"{name}@{version}" if name and version else None

    source_hint = None
    for raw_path in (meta.get("root_path"), meta.get("source"), info.get("source")):
        if isinstance(raw_path, str) and raw_path:
            source_hint = Path(raw_path).name
            break

    candidates = [combined, path_namespace, name, version, source_hint]

    for candidate in candidates:
        cleaned = _sanitize_fragment(candidate)
        if cleaned:
            return cleaned

    digest = hashlib.sha256(graph_id.encode("utf-8")).hexdigest()[:12]
    return f"anon_{digest}"


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
    return f"//dep:{namespace}:{body}"
