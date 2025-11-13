"""CMake command handlers for processing different CMake commands.

This package provides a modular command handler architecture where each
CMake command type has a dedicated handler class.
"""

from .base import CommandHandler
from .target import TargetCommandHandler
from .linking import LinkingCommandHandler
from .sources import SourcesCommandHandler
from .properties import PropertiesCommandHandler
from .includes import IncludesCommandHandler
from .variables import VariablesCommandHandler
from .packages import PackagesCommandHandler
from .files import FilesCommandHandler

__all__ = [
    "CommandHandler",
    "TargetCommandHandler",
    "LinkingCommandHandler",
    "SourcesCommandHandler",
    "PropertiesCommandHandler",
    "IncludesCommandHandler",
    "VariablesCommandHandler",
    "PackagesCommandHandler",
    "FilesCommandHandler",
]
