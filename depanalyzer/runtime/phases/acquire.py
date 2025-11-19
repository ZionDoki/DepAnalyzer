"""
ACQUIRE phase implementation.

This phase is responsible for acquiring the source code from local paths
or Git repositories and preparing the workspace for analysis.
"""

import logging

from depanalyzer.runtime.phases.base import BasePhase
from depanalyzer.runtime.context import TransactionContext

logger = logging.getLogger("depanalyzer.transaction.phase.acquire")


class AcquirePhase(BasePhase):
    """
    ACQUIRE phase: Acquire source code.

    This phase:
    1. Calls workspace.acquire() to download/prepare source code
    2. Generates a graph_id if not provided
    3. Logs the acquired workspace path

    Critical: True - If source acquisition fails, the transaction must halt.
    """

    IS_CRITICAL = True

    def execute(self, context: TransactionContext) -> None:
        """
        Execute ACQUIRE phase logic.

        Args:
            context: TransactionContext with workspace and source information
        """
        # Acquire the source code
        root_path = self.state.workspace.acquire()
        logger.info("Workspace acquired at: %s", root_path)

        # Generate graph_id if not provided
        if not self.state.graph_id:
            self.state.graph_id = f"graph_{self.state.workspace.get_signature()}"
            logger.info("Generated graph_id: %s", self.state.graph_id)
