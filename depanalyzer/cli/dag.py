"""CLI command to inspect and validate the global package-level DAG.

This command reads the GlobalDAG persisted alongside cached graphs and
reports any dependency cycles across repositories. When requested, it
can also fail the process so that CI pipelines can enforce acyclicity.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from depanalyzer.graph.global_dag import GlobalDAG
from depanalyzer.graph.registry import GraphRegistry

logger = logging.getLogger("depanalyzer.cli.dag")


def dag_command(args) -> int:
    """Execute DAG inspection / validation command.

    Args:
        args: Parsed command-line arguments.

    Returns:
        int: Exit code (0 for success, non-zero for failure).
    """
    try:
        cache_dir_arg = getattr(args, "cache_dir", None)
        limit_arg = getattr(args, "limit", None)
        fail_on_cycle = getattr(args, "fail_on_cycle", False)

        # Determine graphs root. cache_dir is expected to point to the
        # per-project cache directory used during scan, typically:
        #   .dep_cache/<source_stem>
        # Graphs and GlobalDAG are stored under <cache_dir>/graphs.
        if cache_dir_arg:
            cache_dir = Path(cache_dir_arg).expanduser().resolve()
        else:
            cache_dir = Path(".dep_cache").expanduser().resolve()

        graphs_root = cache_dir / "graphs"
        graphs_root.mkdir(parents=True, exist_ok=True)

        logger.info("Using graphs root for DAG validation: %s", graphs_root)

        dag = GlobalDAG.get_instance(cache_dir=graphs_root)

        # Interpret limit: <= 0 means "no limit".
        limit: int | None
        if isinstance(limit_arg, int) and limit_arg > 0:
            limit = limit_arg
        else:
            limit = None

        cycles: List[List[str]] = dag.get_cycles(limit=limit)
        if not cycles:
            logger.info("GlobalDAG has no dependency cycles")
            return 0

        logger.warning("Detected %d cycle(s) in GlobalDAG", len(cycles))

        # Load registry so we can show basic source information for each graph.
        registry = GraphRegistry.get_instance(cache_root=graphs_root)

        for idx, cycle in enumerate(cycles, start=1):
            # Present a closed loop for readability: A -> B -> C -> A
            if cycle and cycle[0] != cycle[-1]:
                pretty_cycle = cycle + [cycle[0]]
            else:
                pretty_cycle = cycle

            logger.warning("Cycle %d: %s", idx, " -> ".join(pretty_cycle))

            for gid in cycle:
                entry = registry.get_entry(gid)
                summary = entry.get("summary", {}) if entry else {}
                source = summary.get("source") or "unknown"
                logger.warning("    - %s (source: %s)", gid, source)

        if fail_on_cycle:
            logger.error(
                "GlobalDAG validation failed: dependency cycles detected "
                "(use --fail-on-cycle=false to ignore in CI)."
            )
            return 1

        return 0

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("DAG command failed: %s", e, exc_info=True)
        return 1
