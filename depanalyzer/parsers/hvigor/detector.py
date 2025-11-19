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
        "**/oh-package-lock.json5",
        "**/hvigor-config.json5",
    ]

    def detect(self) -> List[Path]:
        """Detect all Hvigor configuration files in the workspace.

        Returns:
            List of detected configuration file paths.
        """
        detected: List[Path] = []

        # Allow configuration to override or refine search behaviour.
        patterns = list(self.TARGET_PATTERNS)
        ignore_node_modules = False
        include_root_dirs: List[Path] = []

        cfg = getattr(self, "config", None)
        if cfg is not None:
            # Optional: restrict search to a subset of root‑level directories.
            root_dirs = getattr(cfg, "include_root_dirs", None)
            if isinstance(root_dirs, list):
                include_root_dirs = [
                    (self.workspace_root / d) for d in root_dirs
                ]
            ignore_node_modules = bool(getattr(cfg, "ignore_node_modules", False))

        search_roots: List[Path]
        if include_root_dirs:
            search_roots = [p for p in include_root_dirs if p.exists()]
        else:
            search_roots = [self.workspace_root]

        for pattern in patterns:
            files: List[Path] = []
            for root in search_roots:
                for file_path in root.rglob(pattern):
                    if ignore_node_modules and "node_modules" in file_path.parts:
                        continue
                    files.append(file_path)

            detected.extend(files)

            # Publish detection events for each found file
            for file_path in files:
                # Determine target type based on file name
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
        elif file_name == "module.json5":
            return "module_config"
        elif file_name == "oh-package-lock.json5":
            return "lock_file"
        elif file_name == "hvigor-config.json5":
            return "hvigor_config"
        else:
            return "unknown"
