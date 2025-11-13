"""Dependency detector for discovering dependency declarations.

Detects dependency declaration files (oh-package.json5, package.json, etc.)
and publishes detection events.
"""

import logging
from pathlib import Path
from typing import List

from parsers.base import BaseDetector
from runtime.eventbus import Event, EventBus, EventType

logger = logging.getLogger("depanalyzer.parsers.deps.detector")


class DepsDetector(BaseDetector):
    """Detector for dependency declaration files.

    Scans workspace for dependency declarations in various formats:
    - oh-package.json5 (Hvigor/OHPM)
    - oh-package-lock.json5 (Hvigor/OHPM lockfile)
    - package.json (npm)
    - requirements.txt (pip)
    - pom.xml (Maven)
    """

    NAME = "deps"

    # Patterns for dependency declaration files
    DEPENDENCY_PATTERNS = [
        "**/oh-package.json5",
        "**/oh-package-lock.json5",
        "**/hvigor-config.json5",
        "**/package.json",
        "**/requirements.txt",
        "**/pom.xml",
        "**/Cargo.toml",
    ]

    def detect(self) -> List[Path]:
        """Detect dependency declaration files in workspace.

        Returns:
            List[Path]: List of detected dependency declaration files.
        """
        detected_files = []

        for pattern in self.DEPENDENCY_PATTERNS:
            files = list(self.workspace_root.rglob(pattern))
            detected_files.extend(files)

        logger.info("Detected %d dependency declaration files", len(detected_files))

        # Publish detection events
        for file_path in detected_files:
            event = Event(
                event_type=EventType.TARGET_DETECTED,
                source=self.NAME,
                data={
                    "target_path": str(file_path),
                    "target_type": "dependency_declaration",
                    "file_name": file_path.name,
                },
            )
            self.publish_detection_event(event)
            logger.debug("Detected dependency file: %s", file_path)

        return detected_files
