"""Maven detector scanning for pom.xml files."""

import logging
from pathlib import Path
from typing import List, Set

from depanalyzer.parsers.base import BaseDetector
from depanalyzer.runtime.eventbus import Event, EventType

logger = logging.getLogger("depanalyzer.parsers.maven.detector")


class MavenDetector(BaseDetector):
    """Detector for Maven POM files."""

    NAME = "maven"
    ECOSYSTEM = "maven"
    TARGET_NAME = "pom.xml"

    def detect(self) -> List[Path]:
        """Detect pom.xml files under the workspace."""
        detected: List[Path] = []
        ignore_dirs: Set[str] = {"target"}

        cfg = getattr(self, "config", None)
        custom_ignore = getattr(cfg, "ignore_dirs", None)
        if isinstance(custom_ignore, list):
            ignore_dirs.update({d for d in custom_ignore if isinstance(d, str)})

        for path in self.workspace_root.rglob(self.TARGET_NAME):
            if any(part in ignore_dirs for part in path.parts):
                continue

            detected.append(path)
            event = Event(
                event_type=EventType.TARGET_DETECTED,
                source=self.NAME,
                data={
                    "target_path": str(path),
                    "target_type": "maven_pom",
                    "file_name": path.name,
                    "parser_name": self.NAME,
                },
            )
            self.publish_detection_event(event)
            logger.debug("Detected Maven POM: %s", path)

        logger.info("MavenDetector found %d pom.xml files", len(detected))
        return detected


__all__ = ["MavenDetector"]
