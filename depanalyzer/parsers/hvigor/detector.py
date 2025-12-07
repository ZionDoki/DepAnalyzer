"""HVigor configuration detector for detecting Hvigor/ArkTS project files."""

import logging
from pathlib import Path
from typing import List

from depanalyzer.parsers.base import BaseDetector
from depanalyzer.runtime.eventbus import Event, EventType

logger = logging.getLogger("depanalyzer.parsers.hvigor.detector")


class HvigorDetector(BaseDetector):
    """Detector for HVigor/ArkTS project configuration files."""

    NAME = "hvigor"
    ECOSYSTEM = "hvigor"
    TARGET_PATTERNS = [
        "**/build-profile.json5",
        "**/oh-package.json5",
        "**/module.json5",
        "**/module.json",
        "**/oh-package-lock.json5",
        "**/hvigor-config.json5",
    ]
    DEFAULT_IGNORE_PATTERNS = [
        "**/node_modules/**",
        "**/build/**",
        "**/.hvigor/**",
    ]

    def detect(self) -> List[Path]:
        """Detect all Hvigor configuration files in the workspace.

        Returns:
            List of detected configuration file paths.
        """
        # Build ignore patterns
        ignore_patterns = list(self.DEFAULT_IGNORE_PATTERNS)

        cfg = getattr(self, "config", None)
        if cfg is not None:
            # Add node_modules ignore if configured
            if not bool(getattr(cfg, "ignore_node_modules", True)):
                # Remove node_modules from ignore if explicitly disabled
                ignore_patterns = [
                    p for p in ignore_patterns if "node_modules" not in p
                ]

        # Use scan_workspace which respects .gitignore
        detected = self.scan_workspace(
            patterns=self.TARGET_PATTERNS,
            ignore_patterns=ignore_patterns,
            recursive=True,
        )

        # Publish detection events for each found file
        for file_path in detected:
            target_type = self._classify_file_type(file_path.name)

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

        logger.info("HvigorDetector found %d configuration files", len(detected))
        return detected

    def _classify_file_type(self, file_name: str) -> str:
        """Classify the type of Hvigor configuration file.

        Args:
            file_name: Name of the configuration file.

        Returns:
            Classification string for the file type.
        """
        if file_name == "build-profile.json5":
            return "build_profile"
        elif file_name == "oh-package.json5":
            return "package_config"
        elif file_name in ("module.json5", "module.json"):
            return "module_config"
        elif file_name == "oh-package-lock.json5":
            return "lock_file"
        elif file_name == "hvigor-config.json5":
            return "hvigor_config"
        else:
            return "unknown"
