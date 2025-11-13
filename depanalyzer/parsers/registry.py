"""Ecosystem registry for managing detectors, parsers, and dependency fetchers.

Each ecosystem (cpp, hvigor, npm, etc.) registers its three components here.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Type

from depanalyzer.parsers.base import (
    BaseCodeParser,
    BaseDepFetcher,
    BaseDetector,
    BaseParser,
)

logger = logging.getLogger("depanalyzer.parsers.registry")


class EcosystemRegistry:
    """Global registry for ecosystem components.

    Manages registration and discovery of Detector, Parser, and DepFetcher
    implementations for each supported ecosystem.
    """

    _instance: Optional["EcosystemRegistry"] = None

    def __init__(self) -> None:
        """Initialize the registry."""
        # ecosystem -> Detector class
        self._detectors: Dict[str, Type[BaseDetector]] = {}

        # ecosystem -> Parser class
        self._parsers: Dict[str, Type[BaseParser]] = {}

        # ecosystem -> CodeParser class
        self._code_parsers: Dict[str, Type[BaseCodeParser]] = {}

        # ecosystem -> DepFetcher class
        self._dep_fetchers: Dict[str, Type[BaseDepFetcher]] = {}

        logger.info("Ecosystem registry initialized")

    @classmethod
    def get_instance(cls) -> "EcosystemRegistry":
        """Get singleton instance.

        Returns:
            EcosystemRegistry: Global registry instance.
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register_detector(
        self, ecosystem: str, detector_class: Type[BaseDetector]
    ) -> None:
        """Register a detector for an ecosystem.

        Args:
            ecosystem: Ecosystem identifier (e.g., 'cpp', 'hvigor').
            detector_class: Detector class to register.
        """
        if ecosystem in self._detectors:
            logger.warning(
                "Overwriting existing detector for ecosystem '%s': %s -> %s",
                ecosystem,
                self._detectors[ecosystem].__name__,
                detector_class.__name__,
            )

        self._detectors[ecosystem] = detector_class
        logger.info(
            "Registered detector for '%s': %s", ecosystem, detector_class.__name__
        )

    def register_parser(self, ecosystem: str, parser_class: Type[BaseParser]) -> None:
        """Register a parser for an ecosystem.

        Args:
            ecosystem: Ecosystem identifier.
            parser_class: Parser class to register.
        """
        if ecosystem in self._parsers:
            logger.warning(
                "Overwriting existing parser for ecosystem '%s': %s -> %s",
                ecosystem,
                self._parsers[ecosystem].__name__,
                parser_class.__name__,
            )

        self._parsers[ecosystem] = parser_class
        logger.info("Registered parser for '%s': %s", ecosystem, parser_class.__name__)

    def register_dep_fetcher(
        self, ecosystem: str, fetcher_class: Type[BaseDepFetcher]
    ) -> None:
        """Register a dependency fetcher for an ecosystem.

        Args:
            ecosystem: Ecosystem identifier.
            fetcher_class: DepFetcher class to register.
        """
        if ecosystem in self._dep_fetchers:
            logger.warning(
                "Overwriting existing dep_fetcher for ecosystem '%s': %s -> %s",
                ecosystem,
                self._dep_fetchers[ecosystem].__name__,
                fetcher_class.__name__,
            )

        self._dep_fetchers[ecosystem] = fetcher_class
        logger.info(
            "Registered dep_fetcher for '%s': %s", ecosystem, fetcher_class.__name__
        )

    def register_code_parser(
        self, ecosystem: str, code_parser_class: Type[BaseCodeParser]
    ) -> None:
        """Register a code parser for an ecosystem.

        Args:
            ecosystem: Ecosystem identifier.
            code_parser_class: CodeParser class to register.
        """
        if ecosystem in self._code_parsers:
            logger.warning(
                "Overwriting existing code_parser for ecosystem '%s': %s -> %s",
                ecosystem,
                self._code_parsers[ecosystem].__name__,
                code_parser_class.__name__,
            )

        self._code_parsers[ecosystem] = code_parser_class
        logger.info(
            "Registered code_parser for '%s': %s", ecosystem, code_parser_class.__name__
        )

    def register_ecosystem(
        self,
        ecosystem: str,
        detector_class: Type[BaseDetector],
        parser_class: Type[BaseParser],
        fetcher_class: Type[BaseDepFetcher],
        code_parser_class: Optional[Type[BaseCodeParser]] = None,
    ) -> None:
        """Register all components for an ecosystem at once.

        Args:
            ecosystem: Ecosystem identifier.
            detector_class: Detector class.
            parser_class: Parser class.
            fetcher_class: DepFetcher class.
            code_parser_class: Optional CodeParser class.
        """
        self.register_detector(ecosystem, detector_class)
        self.register_parser(ecosystem, parser_class)
        self.register_dep_fetcher(ecosystem, fetcher_class)
        if code_parser_class is not None:
            self.register_code_parser(ecosystem, code_parser_class)
        logger.info("Registered complete ecosystem: %s", ecosystem)

    def get_detector(self, ecosystem: str) -> Optional[Type[BaseDetector]]:
        """Get detector class for an ecosystem.

        Args:
            ecosystem: Ecosystem identifier.

        Returns:
            Optional[Type[BaseDetector]]: Detector class or None if not found.
        """
        return self._detectors.get(ecosystem)

    def get_parser(self, ecosystem: str) -> Optional[Type[BaseParser]]:
        """Get parser class for an ecosystem.

        Args:
            ecosystem: Ecosystem identifier.

        Returns:
            Optional[Type[BaseParser]]: Parser class or None if not found.
        """
        return self._parsers.get(ecosystem)

    def get_dep_fetcher(self, ecosystem: str) -> Optional[Type[BaseDepFetcher]]:
        """Get dependency fetcher class for an ecosystem.

        Args:
            ecosystem: Ecosystem identifier.

        Returns:
            Optional[Type[BaseDepFetcher]]: DepFetcher class or None if not found.
        """
        return self._dep_fetchers.get(ecosystem)

    def get_code_parser(self, ecosystem: str) -> Optional[Type[BaseCodeParser]]:
        """Get code parser class for an ecosystem.

        Args:
            ecosystem: Ecosystem identifier.

        Returns:
            Optional[Type[BaseCodeParser]]: CodeParser class or None if not found.
        """
        return self._code_parsers.get(ecosystem)

    def list_ecosystems(self) -> List[str]:
        """List all registered ecosystems.

        Returns:
            List[str]: List of ecosystem identifiers.
        """
        # Union of all registered ecosystems
        ecosystems = set()
        ecosystems.update(self._detectors.keys())
        ecosystems.update(self._parsers.keys())
        ecosystems.update(self._code_parsers.keys())
        ecosystems.update(self._dep_fetchers.keys())
        return sorted(ecosystems)

    def is_complete(self, ecosystem: str) -> bool:
        """Check if an ecosystem has all three components registered.

        Args:
            ecosystem: Ecosystem identifier.

        Returns:
            bool: True if detector, parser, and fetcher are all registered.
        """
        return (
            ecosystem in self._detectors
            and ecosystem in self._parsers
            and ecosystem in self._dep_fetchers
        )

    def get_incomplete_ecosystems(self) -> Dict[str, Dict[str, bool]]:
        """Get ecosystems that are missing components.

        Returns:
            Dict[str, Dict[str, bool]]: Map of ecosystem to missing components.
        """
        incomplete = {}
        for ecosystem in self.list_ecosystems():
            missing = {
                "detector": ecosystem not in self._detectors,
                "parser": ecosystem not in self._parsers,
                "dep_fetcher": ecosystem not in self._dep_fetchers,
                "code_parser": ecosystem not in self._code_parsers,
            }
            if any(missing.values()):
                incomplete[ecosystem] = missing
        return incomplete


# Convenience function for registration
def register_ecosystem(
    ecosystem: str,
    detector_class: Type[BaseDetector],
    parser_class: Type[BaseParser],
    fetcher_class: Type[BaseDepFetcher],
    code_parser_class: Optional[Type[BaseCodeParser]] = None,
) -> None:
    """Register all components for an ecosystem.

    Convenience wrapper around EcosystemRegistry.register_ecosystem().

    Args:
        ecosystem: Ecosystem identifier.
        detector_class: Detector class.
        parser_class: Parser class.
        fetcher_class: DepFetcher class.
        code_parser_class: Optional CodeParser class.
    """
    registry = EcosystemRegistry.get_instance()
    registry.register_ecosystem(
        ecosystem, detector_class, parser_class, fetcher_class, code_parser_class
    )
