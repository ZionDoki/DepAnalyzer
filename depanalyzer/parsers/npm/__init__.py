"""NPM ecosystem parser package.

This package provides dependency analysis for Node.js/npm projects,
including:
- Detection of package.json files
- Parsing of npm package configurations
- JavaScript/TypeScript code parsing for import/require extraction
- npm registry interaction for dependency fetching
- Module linking and native addon bridging
"""

from depanalyzer.parsers.npm.detector import NpmDetector
from depanalyzer.parsers.npm.config_parser import NpmParser
from depanalyzer.parsers.npm.code_parser import NpmCodeParser
from depanalyzer.parsers.npm.dep_fetcher import NpmDepFetcher
from depanalyzer.parsers.npm.linker import NpmLinker
from depanalyzer.parsers.npm.code_dependency_mapper import NpmCodeDependencyMapper

__all__ = [
    "NpmDetector",
    "NpmParser",
    "NpmCodeParser",
    "NpmDepFetcher",
    "NpmLinker",
    "NpmCodeDependencyMapper",
]
