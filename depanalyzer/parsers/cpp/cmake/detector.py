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
    ECOSYSTEM = "cpp"
    TARGET_PATTERNS = [
        "**/CMakeLists.txt",
        "**/*.cmake",
    ]

    def detect(self) -> List[Path]:
        """Detect all CMake files in the workspace.

        Returns:
            List of detected CMake file paths.
        """
        detected: List[Path] = []

        cfg = getattr(self, "config", None)
        ignore_build_dirs = bool(getattr(cfg, "ignore_build_dirs", False)) if cfg else False
        ignore_third_party_dirs = bool(getattr(cfg, "ignore_third_party_dirs", False)) if cfg else False

        extra_ignores = []
        if ignore_build_dirs:
            extra_ignores.append("build")
        if ignore_third_party_dirs:
            extra_ignores.extend(["third_party", "3rdparty", "third-party"])

        detected = self.scan_workspace(self.TARGET_PATTERNS, ignore_patterns=extra_ignores)

        # Publish detection events for each found file
        for file_path in detected:
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
            logger.debug("Detected %s: %s", target_type, file_path)

        logger.info("CMakeDetector found %d CMake files", len(detected))
        return detected
