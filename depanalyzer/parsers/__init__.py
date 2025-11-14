"""Parsers package.

Language‑specific code and config parsers are registered from here.
"""

import logging

from depanalyzer.parsers.registry import register_ecosystem

logger = logging.getLogger("depanalyzer.parsers")

# Import CPP ecosystem components
# Note: Some CPP modules still have import path issues (to be fixed in Tasks 6-7)
try:
    from depanalyzer.parsers.cpp.cmake.detector import CMakeDetector
    from depanalyzer.parsers.cpp.config_parser import CMakeParser
    from depanalyzer.parsers.cpp.dep_fetcher import CppDepFetcher
    from depanalyzer.parsers.cpp.code_parser import CppCodeParser

    _CPP_AVAILABLE = True
except ImportError as e:
    logger.warning("CPP ecosystem imports failed (will be fixed in Tasks 6-7): %s", e)
    _CPP_AVAILABLE = False

# Import Hvigor ecosystem components
# Note: HvigorParser currently has import path issues (to be fixed in Tasks 8-10)
try:
    from depanalyzer.parsers.hvigor.detector import HvigorDetector
    from depanalyzer.parsers.hvigor.config_parser import HvigorParser
    from depanalyzer.parsers.hvigor.dep_fetcher import HvigorDepFetcher
    from depanalyzer.parsers.hvigor.code_parser import HvigorCodeParser

    _HVIGOR_AVAILABLE = True
except ImportError as e:
    logger.warning("Hvigor ecosystem imports failed (will be fixed in Tasks 8-10): %s", e)
    _HVIGOR_AVAILABLE = False


# Register CPP ecosystem (CMake-based)
if _CPP_AVAILABLE:
    register_ecosystem(
        ecosystem="cpp",
        detector_class=CMakeDetector,
        parser_class=CMakeParser,
        fetcher_class=CppDepFetcher,
        code_parser_class=CppCodeParser,
    )
    logger.info("Registered CPP ecosystem")

# Register Hvigor ecosystem (OpenHarmony)
if _HVIGOR_AVAILABLE:
    register_ecosystem(
        ecosystem="hvigor",
        detector_class=HvigorDetector,
        parser_class=HvigorParser,
        fetcher_class=HvigorDepFetcher,
        code_parser_class=HvigorCodeParser,
    )
    logger.info("Registered Hvigor ecosystem")

if not _CPP_AVAILABLE and not _HVIGOR_AVAILABLE:
    logger.warning(
        "No ecosystems registered! "
        "Import paths need to be fixed in Tasks 6-10 before ecosystems can be registered."
    )


__all__ = [
    "register_ecosystem",
]

if _CPP_AVAILABLE:
    __all__.extend(
        [
            "CMakeDetector",
            "CMakeParser",
            "CppDepFetcher",
            "CppCodeParser",
        ]
    )

if _HVIGOR_AVAILABLE:
    __all__.extend(
        [
            "HvigorDetector",
            "HvigorParser",
            "HvigorDepFetcher",
            "HvigorCodeParser",
        ]
    )
