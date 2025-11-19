"""Registry for per-ecosystem configuration factories.

This registry enables parser packages to maintain their own ecosystem
configuration models while providing a unified registration point that
GraphBuildConfig can consume when loading user configuration files
(.toml/.json or inline config strings).
"""

# Registry methods catch unexpected factory errors to avoid crashing import-time wiring.
# pylint: disable=broad-exception-caught

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

import logging

logger = logging.getLogger("depanalyzer.runtime.ecosystem_config_registry")


class EcosystemConfigRegistry:
    """Global registry for ecosystem configuration factories.

    Each ecosystem (cpp, hvigor, python, etc.) may register a factory
    function that knows how to build its configuration object from a
    plain dictionary (parsed from TOML/JSON).

    The registry is intentionally minimal and independent from the
    graph-building lifecycle so that parser packages can call it at
    import time without causing heavy dependencies.
    """

    _factories: Dict[str, Callable[[Dict[str, Any]], Any]] = {}

    @classmethod
    def register_config_factory(
        cls,
        ecosystem: str,
        factory: Callable[[Dict[str, Any]], Any],
    ) -> None:
        """Register a configuration factory for an ecosystem.

        Args:
            ecosystem: Ecosystem identifier (e.g., "hvigor", "cpp").
            factory: Callable that accepts a dict and returns an
                ecosystem-specific configuration object.
        """
        if ecosystem in cls._factories:
            logger.warning(
                "Overwriting existing config factory for ecosystem '%s'", ecosystem
            )
        cls._factories[ecosystem] = factory
        logger.info("Registered config factory for ecosystem '%s'", ecosystem)

    @classmethod
    def from_dict(cls, ecosystem: str, data: Dict[str, Any]) -> Optional[Any]:
        """Create an ecosystem configuration object from a dict.

        Args:
            ecosystem: Ecosystem identifier.
            data: Parsed configuration mapping (typically from TOML/JSON).

        Returns:
            Ecosystem-specific configuration object or None if no
            factory has been registered.
        """
        factory = cls._factories.get(ecosystem)
        if factory is None:
            logger.debug(
                "No config factory registered for ecosystem '%s'; skipping", ecosystem
            )
            return None
        try:
            return factory(data or {})
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Config factory for ecosystem '%s' failed: %s", ecosystem, exc, exc_info=True
            )
            return None


__all__ = ["EcosystemConfigRegistry"]
