"""Maven detector scanning for pom.xml files."""

import logging
from pathlib import Path
from typing import List

from depanalyzer.parsers.base import BaseDetector
from depanalyzer.runtime.eventbus import Event, EventType

logger = logging.getLogger("depanalyzer.parsers.maven.detector")


class MavenDetector(BaseDetector):
    """Detector for Maven POM files."""

    NAME = "maven"
    ECOSYSTEM = "maven"
    TARGET_NAME = "pom.xml"
    DEFAULT_IGNORE_PATTERNS = ["**/target/**"]

    def detect(self) -> List[Path]:
        """Detect pom.xml files under the workspace."""
        # Build ignore patterns
        ignore_patterns = list(self.DEFAULT_IGNORE_PATTERNS)

        cfg = getattr(self, "config", None)
        custom_ignore = getattr(cfg, "ignore_dirs", None)
        if isinstance(custom_ignore, list):
            ignore_patterns.extend(
                f"**/{d}/**" for d in custom_ignore if isinstance(d, str)
            )

        # Use scan_workspace which respects .gitignore
        detected = self.scan_workspace(
            patterns=[f"**/{self.TARGET_NAME}"],
            ignore_patterns=ignore_patterns,
            recursive=True,
        )

        # Publish detection events
        for path in detected:
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
