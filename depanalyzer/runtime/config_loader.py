"""Helpers for loading graph build configuration from TOML/JSON sources.

This module provides a single entry point `load_graph_build_config`
that accepts various configuration sources:

* None -> default GraphBuildConfig
* dict -> GraphBuildConfig.from_dict
* Path / path-like string -> load .toml/.json from filesystem
* Inline JSON/TOML strings
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Union
import json
import logging

from depanalyzer.runtime.graph_config import GraphBuildConfig

logger = logging.getLogger("depanalyzer.runtime.config_loader")

ConfigSource = Union[str, Path, Dict[str, Any], None]


def _parse_toml(text: str) -> Dict[str, Any]:
    """Parse TOML text into a dict.

    Uses stdlib tomllib on Python 3.11+ and falls back to `tomli` when
    available on older interpreters. Import is intentionally local to
    avoid hard dependency at import time.
    """
    try:
        import tomllib  # type: ignore[attr-defined]
        return tomllib.loads(text)
    except ImportError as exc:  # pragma: no cover - Python <3.11 path
        try:
            import tomli  # type: ignore[import-not-found]

            return tomli.loads(text)
        except Exception as inner_exc:  # noqa: BLE001
            raise RuntimeError(
                "TOML configuration requires Python 3.11+ (tomllib) or the "
                "`tomli` package installed"
            ) from inner_exc


def load_graph_build_config(source: ConfigSource) -> GraphBuildConfig:
    """Load GraphBuildConfig from various configuration sources.

    Args:
        source: One of:
            * None: returns GraphBuildConfig.default()
            * dict: treated as already-parsed configuration mapping
            * str/Path: either a filesystem path to a .toml/.json file,
              or an inline TOML/JSON string (auto-detected)

    Returns:
        GraphBuildConfig instance.
    """
    if source is None:
        logger.debug("No config source provided; using default GraphBuildConfig")
        return GraphBuildConfig.default()

    # Already parsed mapping
    if isinstance(source, dict):
        logger.debug("Loading GraphBuildConfig from provided dict")
        return GraphBuildConfig.from_dict(source)

    # Path or string (file path or inline text)
    if isinstance(source, (str, Path)):
        path = Path(source)
        text: Optional[str] = None
        fmt: Optional[str] = None

        if path.exists():
            # Treat as filesystem path
            text = path.read_text(encoding="utf-8")
            suffix = path.suffix.lower()
            if suffix in {".toml", ".tml"}:
                fmt = "toml"
            elif suffix == ".json":
                fmt = "json"
            else:
                # Fallback: guess from content
                stripped = text.lstrip()
                fmt = "json" if stripped.startswith(("{", "[")) else "toml"
            logger.info("Loading configuration from file: %s (fmt=%s)", path, fmt)
        else:
            # Inline string; auto-detect format
            text = str(source)
            stripped = text.lstrip()
            fmt = "json" if stripped.startswith(("{", "[")) else "toml"
            logger.info("Loading configuration from inline %s string", fmt)

        assert text is not None and fmt is not None

        if fmt == "json":
            data = json.loads(text)
        else:
            data = _parse_toml(text)

        if not isinstance(data, dict):
            raise ValueError("Top-level configuration must be a mapping/dict")

        return GraphBuildConfig.from_dict(data)

    raise TypeError(f"Unsupported config source type: {type(source)!r}")


__all__ = ["load_graph_build_config"]

