"""
DETECT phase implementation.

This phase detects build targets (e.g., package.json, pom.xml) across
all registered ecosystems using parallel detection workers.
"""

import logging
from pathlib import Path
from typing import Dict, List

from depanalyzer.parsers.registry import EcosystemRegistry
from depanalyzer.runtime.lifecycle import LifecyclePhase
from depanalyzer.runtime.phases.base import BasePhase
from depanalyzer.runtime.context import TransactionContext
from depanalyzer.runtime.worker import Task, TaskPriority

logger = logging.getLogger("depanalyzer.transaction.phase.detect")

# Safe exceptions for detection failures
_SAFE_EXCEPTIONS = (
    RuntimeError,
    ValueError,
    TypeError,
    AttributeError,
    KeyError,
    IndexError,
    OSError,
    ImportError,
    LookupError,
)


class DetectPhase(BasePhase):
    """
    DETECT phase: Detect build targets across all ecosystems.

    This phase:
    1. Gets all registered ecosystems from EcosystemRegistry
    2. Creates parallel detection tasks for each ecosystem
    3. Executes tasks using Worker pool
    4. Stores detected targets in state

    Critical: True - If detection fails, no targets to parse.
    """

    IS_CRITICAL = True

    def execute(self, context: TransactionContext) -> None:
        """
        Execute DETECT phase logic.

        Args:
            context: TransactionContext with workspace information
        """
        registry = EcosystemRegistry.get_instance()

        # Get all registered detector classes
        ecosystems = registry.list_ecosystems()
        logger.info("Registered ecosystems in registry: %s", ecosystems)

        if not ecosystems:
            logger.warning("No ecosystems registered, skipping detection")
            return

        logger.info("Running detectors for %d ecosystems", len(ecosystems))

        # Create detection tasks
        for ecosystem in ecosystems:
            detector_class = registry.get_detector(ecosystem)
            if detector_class is None:
                logger.debug("No detector registered for ecosystem: %s", ecosystem)
                continue

            # Create detector task
            def detect_task(eco=ecosystem, det_cls=detector_class):
                """Detection task wrapper."""
                try:
                    detect_cfg = self.state.graph_build_config.get_detect_config(eco)
                    detector = det_cls(
                        self.state.workspace.root_path,
                        self.state.eventbus,
                        config=detect_cfg,
                    )
                    targets = detector.detect()
                    logger.info("Detector %s found %d targets", eco, len(targets))
                    return {"ecosystem": eco, "targets": targets, "success": True}
                except _SAFE_EXCEPTIONS as e:
                    logger.error("Detector %s failed: %s", eco, e)
                    return {
                        "ecosystem": eco,
                        "targets": [],
                        "success": False,
                        "error": str(e),
                    }

            task = Task(
                task_id=f"detect_{ecosystem}",
                func=detect_task,
                priority=TaskPriority.HIGH,
            )
            self.state.worker.enqueue(task)

        # Log task count before execution
        task_count = self.state.worker.queued_task_count()
        logger.info("Enqueued %d detection tasks for execution", task_count)

        # Execute all detection tasks and collect results
        results = self.state.worker.run_all()
        logger.info("Detection tasks completed: %d results received", len(results))

        # Process results and store detected targets
        detected_targets: Dict[str, List[Path]] = {}
        completed_count = 0

        for _task_id, result in results.items():
            if result.success and result.result:
                ecosystem = result.result.get("ecosystem")
                targets = result.result.get("targets", [])
                if ecosystem and targets:
                    detected_targets[ecosystem] = targets
                    logger.info(
                        "Stored %d targets for ecosystem: %s", len(targets), ecosystem
                    )

            completed_count += 1
            # Update progress after each ecosystem
            if self.state.progress_manager:
                total_targets = sum(
                    len(targets) for targets in detected_targets.values()
                )

        # Store detected targets in state
        self.state.update_phase_result(
            LifecyclePhase.DETECT, "detected_targets", detected_targets
        )

        total_targets = sum(len(targets) for targets in detected_targets.values())
        logger.info(
            "Detection phase completed: %d ecosystems, %d total targets",
            len(detected_targets),
            total_targets,
        )
