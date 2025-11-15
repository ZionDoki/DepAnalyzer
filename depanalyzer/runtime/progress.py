"""Rich-based progress tracking for transaction execution.

Provides hierarchical progress display with live panel for metrics,
active tasks, and phase execution tracking.
"""

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, Optional, Set

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text

from depanalyzer.runtime.lifecycle import LifecyclePhase

logger = logging.getLogger("depanalyzer.runtime.progress")


@dataclass
class PhaseProgress:
    """Progress tracking for a single lifecycle phase."""

    phase: LifecyclePhase
    task_id: TaskID
    total: int
    completed: int = 0
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    description: str = ""

    @property
    def elapsed(self) -> float:
        """Get elapsed time for this phase."""
        end = self.end_time or time.time()
        return end - self.start_time

    @property
    def is_complete(self) -> bool:
        """Check if phase is complete."""
        return self.end_time is not None


@dataclass
class TransactionMetrics:
    """Real-time metrics for transaction execution."""

    graph_id: str
    source: str
    node_count: int = 0
    edge_count: int = 0
    start_time: float = field(default_factory=time.time)

    @property
    def elapsed(self) -> float:
        """Get total elapsed time."""
        return time.time() - self.start_time


class ProgressManager:
    """Rich-based progress manager with live panel display.

    Features:
    - Hierarchical progress bars for phases/tasks/files
    - Live metrics panel (nodes/edges/time)
    - Active task list (currently running operations)
    - Thread-safe for concurrent updates
    - Graceful degradation if disabled
    """

    def __init__(
        self,
        enabled: bool = True,
        live_panel: bool = True,
        console: Optional[Console] = None,
    ) -> None:
        """Initialize progress manager.

        Args:
            enabled: Enable/disable progress display.
            live_panel: Show live metrics panel.
            console: Rich console (creates new if None).
        """
        self.enabled = enabled
        self.live_panel_enabled = live_panel and enabled
        self.console = console or Console()

        # Thread safety
        self._lock = threading.Lock()

        # Phase tracking
        self._phases: Dict[str, PhaseProgress] = {}
        self._phase_order: list[str] = []

        # Metrics tracking
        self._metrics: Optional[TransactionMetrics] = None

        # Active tasks tracking (for live panel)
        self._active_tasks: Set[str] = set()

        # Rich components
        self._progress: Optional[Progress] = None
        self._live: Optional[Live] = None
        self._update_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        if self.enabled:
            self._init_progress_display()

    def _init_progress_display(self) -> None:
        """Initialize Rich progress bars."""
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=40),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            console=self.console,
            expand=False,
        )

    def start_transaction(
        self,
        graph_id: str,
        source: str,
    ) -> None:
        """Start transaction progress tracking.

        Args:
            graph_id: Graph identifier.
            source: Source path/URL.
        """
        if not self.enabled:
            return

        with self._lock:
            self._metrics = TransactionMetrics(
                graph_id=graph_id,
                source=source,
            )

    def start_phase(
        self,
        phase: LifecyclePhase,
        total: int = 100,
        description: str = "",
    ) -> str:
        """Start tracking a lifecycle phase.

        Args:
            phase: Lifecycle phase enum.
            total: Total items to process.
            description: Human-readable description.

        Returns:
            str: Phase tracking ID.
        """
        if not self.enabled or not self._progress:
            return f"phase_{phase.name}"

        phase_id = f"phase_{phase.name}"

        with self._lock:
            # Create progress task
            task_id = self._progress.add_task(
                description=description or str(phase),
                total=total,
            )

            # Track phase
            self._phases[phase_id] = PhaseProgress(
                phase=phase,
                task_id=task_id,
                total=total,
                description=description or str(phase),
            )
            self._phase_order.append(phase_id)

        return phase_id

    def update_phase(
        self,
        phase_id: str,
        completed: Optional[int] = None,
        advance: int = 0,
        description: Optional[str] = None,
    ) -> None:
        """Update phase progress.

        Args:
            phase_id: Phase tracking ID.
            completed: Absolute completed count.
            advance: Relative advance amount.
            description: Updated description.
        """
        if not self.enabled or not self._progress:
            return

        with self._lock:
            phase = self._phases.get(phase_id)
            if not phase:
                return

            if completed is not None:
                phase.completed = completed
                self._progress.update(
                    phase.task_id,
                    completed=completed,
                )

            if advance > 0:
                phase.completed += advance
                self._progress.advance(phase.task_id, advance)

            if description:
                phase.description = description
                self._progress.update(
                    phase.task_id,
                    description=description,
                )

    def complete_phase(self, phase_id: str) -> None:
        """Mark phase as complete.

        Args:
            phase_id: Phase tracking ID.
        """
        if not self.enabled or not self._progress:
            return

        with self._lock:
            phase = self._phases.get(phase_id)
            if not phase:
                return

            phase.end_time = time.time()
            self._progress.update(
                phase.task_id,
                completed=phase.total,
            )

    def add_active_task(self, task_name: str) -> None:
        """Add task to active task list.

        Args:
            task_name: Human-readable task name.
        """
        if not self.enabled or not self.live_panel_enabled:
            return

        with self._lock:
            self._active_tasks.add(task_name)

    def remove_active_task(self, task_name: str) -> None:
        """Remove task from active task list.

        Args:
            task_name: Human-readable task name.
        """
        if not self.enabled or not self.live_panel_enabled:
            return

        with self._lock:
            self._active_tasks.discard(task_name)

    def update_metrics(
        self,
        node_count: Optional[int] = None,
        edge_count: Optional[int] = None,
    ) -> None:
        """Update graph metrics.

        Args:
            node_count: Total nodes in graph.
            edge_count: Total edges in graph.
        """
        if not self.enabled or not self._metrics:
            return

        with self._lock:
            if node_count is not None:
                self._metrics.node_count = node_count
            if edge_count is not None:
                self._metrics.edge_count = edge_count

    def _build_live_panel(self) -> Panel:
        """Build live display panel with metrics and active tasks.

        Returns:
            Panel: Rich panel with transaction status.
        """
        with self._lock:
            # Build metrics table
            metrics_table = Table.grid(padding=(0, 2))
            metrics_table.add_column(style="cyan", justify="right")
            metrics_table.add_column(style="white")

            if self._metrics:
                metrics_table.add_row(
                    "Graph ID:",
                    Text(self._metrics.graph_id[:12] + "...", style="yellow"),
                )
                metrics_table.add_row(
                    "Source:",
                    Text(self._metrics.source[-40:], style="green"),
                )
                metrics_table.add_row(
                    "Nodes:",
                    Text(f"{self._metrics.node_count:,}", style="magenta"),
                )
                metrics_table.add_row(
                    "Edges:",
                    Text(f"{self._metrics.edge_count:,}", style="magenta"),
                )
                metrics_table.add_row(
                    "Elapsed:",
                    Text(f"{self._metrics.elapsed:.1f}s", style="blue"),
                )

            # Build active tasks panel
            active_tasks_text = Text()
            if self._active_tasks:
                for task in list(self._active_tasks)[:5]:  # Show max 5
                    active_tasks_text.append(f"→ {task}\n", style="dim cyan")
            else:
                active_tasks_text.append("(no active tasks)", style="dim")

            # Combine into group
            group = Group(
                Panel(
                    metrics_table, title="[bold]Metrics[/bold]", border_style="blue"
                ),
                Panel(
                    active_tasks_text,
                    title="[bold]Active Tasks[/bold]",
                    border_style="green",
                ),
            )

            return Panel(
                group,
                title="[bold yellow]Transaction Progress[/bold yellow]",
                border_style="yellow",
            )

    def _update_live_display(self) -> None:
        """Background thread function to update live display."""
        while not self._stop_event.is_set():
            try:
                if self._live and self._progress:
                    with self._lock:
                        renderable = Group(
                            self._progress,
                            self._build_live_panel(),
                        )
                    self._live.update(renderable)
            except Exception as e:
                logger.debug(f"Live display update error: {e}")

            # Sleep with early exit on stop event
            self._stop_event.wait(0.25)

    @contextmanager
    def live_display(self):
        """Context manager for live progress display.

        Yields:
            ProgressManager: Self for chaining.
        """
        if not self.enabled or not self._progress:
            yield self
            return

        try:
            if self.live_panel_enabled:
                # Live panel with auto-refresh
                renderable = Group(
                    self._progress,
                    self._build_live_panel(),
                )

                with Live(
                    renderable,
                    console=self.console,
                    refresh_per_second=4,
                ) as live:
                    self._live = live
                    self._stop_event.clear()

                    # Start update thread
                    self._update_thread = threading.Thread(
                        target=self._update_live_display,
                        daemon=True,
                    )
                    self._update_thread.start()

                    yield self

                    # Stop update thread
                    self._stop_event.set()
                    if self._update_thread:
                        self._update_thread.join(timeout=1.0)
                    self._live = None
                    self._update_thread = None
            else:
                # Simple progress display without live panel
                with self._progress:
                    yield self

        except Exception as e:
            logger.error(f"Progress display error: {e}")
            yield self

    def stop(self) -> None:
        """Stop all progress tracking."""
        self._stop_event.set()

        if self._update_thread and self._update_thread.is_alive():
            self._update_thread.join(timeout=1.0)

        if self._live:
            try:
                self._live.stop()
            except Exception:
                pass
            self._live = None

        if self._progress:
            try:
                self._progress.stop()
            except Exception:
                pass
