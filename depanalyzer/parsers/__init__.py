"""Parsers package.

Language‑specific code and config parsers are registered from here.
"""

import logging

from depanalyzer.parsers.registry import register_ecosystem

logger = logging.getLogger("depanalyzer.parsers")

# Track registration status
_registered_ecosystems = []
_failed_ecosystems = []

# Import CPP ecosystem components
try:
    from depanalyzer.parsers.cpp.cmake.detector import CMakeDetector
    from depanalyzer.parsers.cpp.config_parser import CMakeParser
    from depanalyzer.parsers.cpp.dep_fetcher import CppDepFetcher
    from depanalyzer.parsers.cpp.code_parser import CppCodeParser

    _CPP_AVAILABLE = True
    logger.debug("CPP ecosystem components imported successfully")
except ImportError as e:
    logger.error("CPP ecosystem imports failed: %s", e, exc_info=True)
    logger.error(
        "CPP ecosystem will not be available. "
        "Please ensure all dependencies are installed (tree-sitter, tree-sitter-cmake, etc.)"
    )
    _CPP_AVAILABLE = False
    _failed_ecosystems.append(("cpp", str(e)))

# Import Hvigor ecosystem components
try:
    from depanalyzer.parsers.hvigor.detector import HvigorDetector
    from depanalyzer.parsers.hvigor.config_parser import HvigorParser
    from depanalyzer.parsers.hvigor.dep_fetcher import HvigorDepFetcher
    from depanalyzer.parsers.hvigor.code_parser import HvigorCodeParser

    _HVIGOR_AVAILABLE = True
    logger.debug("Hvigor ecosystem components imported successfully")
except ImportError as e:
    logger.error("Hvigor ecosystem imports failed: %s", e, exc_info=True)
    logger.error(
        "Hvigor ecosystem will not be available. "
        "Please ensure all dependencies are installed (json5, requests, packaging, etc.)"
    )
    _HVIGOR_AVAILABLE = False
    _failed_ecosystems.append(("hvigor", str(e)))


# Register CPP ecosystem (CMake-based)
if _CPP_AVAILABLE:
    try:
        register_ecosystem(
            ecosystem="cpp",
            detector_class=CMakeDetector,
            parser_class=CMakeParser,
            fetcher_class=CppDepFetcher,
            code_parser_class=CppCodeParser,
        )
        logger.info("✓ Successfully registered CPP ecosystem (CMake-based)")
        _registered_ecosystems.append("cpp")
    except Exception as e:
        logger.error("Failed to register CPP ecosystem: %s", e, exc_info=True)
        _failed_ecosystems.append(("cpp", f"Registration failed: {e}"))

# Register Hvigor ecosystem (OpenHarmony)
if _HVIGOR_AVAILABLE:
    try:
        register_ecosystem(
            ecosystem="hvigor",
            detector_class=HvigorDetector,
            parser_class=HvigorParser,
            fetcher_class=HvigorDepFetcher,
            code_parser_class=HvigorCodeParser,
        )
        logger.info("✓ Successfully registered Hvigor ecosystem (OpenHarmony)")
        _registered_ecosystems.append("hvigor")
    except Exception as e:
        logger.error("Failed to register Hvigor ecosystem: %s", e, exc_info=True)
        _failed_ecosystems.append(("hvigor", f"Registration failed: {e}"))

# Summary logging
if _registered_ecosystems:
    logger.info(
        "Ecosystem registration complete: %d ecosystem(s) available: %s",
        len(_registered_ecosystems),
        ", ".join(_registered_ecosystems),
    )
else:
    logger.error(
        "⚠ WARNING: No ecosystems registered! Scanning will not work. "
        "Please check the error messages above and install required dependencies."
    )

if _failed_ecosystems:
    logger.warning(
        "Failed to register %d ecosystem(s): %s",
        len(_failed_ecosystems),
        ", ".join(f"{name} ({reason})" for name, reason in _failed_ecosystems),
    )


def get_registered_ecosystems():
    """Get list of successfully registered ecosystems.

    Returns:
        list: List of ecosystem names that were successfully registered.
    """
    return _registered_ecosystems.copy()


def get_failed_ecosystems():
    """Get list of ecosystems that failed to register.

    Returns:
        list: List of tuples (ecosystem_name, error_message).
    """
    return _failed_ecosystems.copy()


__all__ = [
    "register_ecosystem",
    "get_registered_ecosystems",
    "get_failed_ecosystems",
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
