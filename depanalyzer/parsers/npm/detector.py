"""NPM ecosystem detector.

Detects Node.js/npm projects by scanning for package.json files.
"""

import logging
from pathlib import Path
from typing import List, Optional, Any

from depanalyzer.parsers.base import BaseDetector
from depanalyzer.runtime.eventbus import Event, EventType

logger = logging.getLogger("depanalyzer.parsers.npm.detector")


class NpmDetector(BaseDetector):
    """Detector for npm/Node.js projects.

    Scans the workspace for package.json files, ignoring node_modules
    and common build output directories.
    """

    NAME = "npm_detector"
    ECOSYSTEM = "npm"

    # Default patterns to ignore during detection
    DEFAULT_IGNORE_PATTERNS = [
        "**/node_modules/**",
        "**/dist/**",
        "**/build/**",
        "**/out/**",
        "**/.next/**",
        "**/.nuxt/**",
        "**/coverage/**",
    ]

    def detect(self) -> List[Path]:
        """Detect npm projects in workspace.

        Scans for package.json files while respecting ignore patterns
        from configuration and .gitignore.

        Returns:
            List[Path]: List of detected package.json file paths.
        """
        logger.info("NpmDetector: scanning for package.json files in %s", self.workspace_root)

        # Build ignore patterns
        ignore_patterns = list(self.DEFAULT_IGNORE_PATTERNS)

        # Add config-based ignore patterns
        if self.config:
            if getattr(self.config, "ignore_node_modules", True):
                # Already in default patterns, but ensure it's there
                pass
            extra_ignores = getattr(self.config, "extra_ignore_patterns", None)
            if extra_ignores:
                ignore_patterns.extend(extra_ignores)

        # Scan for package.json files
        patterns = ["**/package.json"]
        detected = self.scan_workspace(
            patterns=patterns,
            ignore_patterns=ignore_patterns,
            recursive=True,
        )

        logger.info("NpmDetector: found %d package.json file(s)", len(detected))

        # Publish detection events for each found package.json
        for pkg_path in detected:
            self._publish_detection(pkg_path)

        return detected

    def _publish_detection(self, package_json_path: Path) -> None:
        """Publish detection event for a package.json file.

        Args:
            package_json_path: Path to the detected package.json file.
        """
        event = Event(
            event_type=EventType.TARGET_DETECTED,
            source=self.NAME,
            data={
                "target_path": str(package_json_path),
                "target_type": "package.json",
                "ecosystem": self.ECOSYSTEM,
            },
        )
        self.publish_detection_event(event)
        logger.debug("NpmDetector: published detection event for %s", package_json_path)
