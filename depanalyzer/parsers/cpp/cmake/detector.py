"""CMake configuration detector for detecting CMake project files."""

import logging
from pathlib import Path
from typing import List

from depanalyzer.parsers.base import BaseDetector
from depanalyzer.runtime.eventbus import Event, EventType

logger = logging.getLogger("depanalyzer.parsers.cpp.cmake.detector")


class CMakeDetector(BaseDetector):
    """Detector for CMake configuration files."""

    NAME = "cmake"
    TARGET_PATTERNS = [
        "**/CMakeLists.txt",
        "**/*.cmake",
    ]

    def detect(self) -> List[Path]:
        """Detect all CMake files in the workspace.

        Returns:
            List of detected CMake file paths.
        """
        detected = []

        for pattern in self.TARGET_PATTERNS:
            files = list(self.workspace_root.rglob(pattern))
            detected.extend(files)

            # Publish detection events for each found file
            for file_path in files:
                # Classify as either root CMakeLists.txt or auxiliary cmake file
                target_type = (
                    "cmake_root"
                    if file_path.name == "CMakeLists.txt"
                    else "cmake_module"
                )

                event = Event(
                    event_type=EventType.TARGET_DETECTED,
                    source=self.NAME,
                    data={
                        "target_path": str(file_path),
                        "target_type": target_type,
                        "file_name": file_path.name,
                        "parser_name": self.NAME,
                    },
                )
                self.publish_detection_event(event)

                logger.debug(f"Detected {target_type}: {file_path}")

        logger.info(f"CMakeDetector found {len(detected)} CMake files")
        return detected
