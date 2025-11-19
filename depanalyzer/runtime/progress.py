"""Rich-based progress tracking for transaction execution.

Provides hierarchical progress display with live panel for metrics,
active tasks, and phase execution tracking.
"""

# Progress manager shields live rendering errors so builds continue even if the UI fails.


import logging
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, Optional, Set, Deque

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
from rich.text import Text

from depanalyzer.runtime.lifecycle import LifecyclePhase

logger = logging.getLogger("depanalyzer.runtime.progress")


class LogBuffer:
    """Thread-safe buffer for log messages to display in Live context.

    Stores recent log messages that will be displayed above the progress bar.
    """

    def __init__(self, max_lines: int = 10):
        """Initialize log buffer.

        Args:
            max_lines: Maximum number of log lines to keep.
        """
        self.max_lines = max_lines
        self._buffer: Deque[str] = deque(maxlen=max_lines)
        self._lock = threading.Lock()

    def add(self, message: str) -> None:
        """Add a log message to the buffer.

        Args:
            message: Log message to add.
        """
        with self._lock:
            self._buffer.append(message)

    def get_lines(self) -> list[str]:
        """Get all buffered log lines.

        Returns:
            List of log messages.
        """
        with self._lock:
            return list(self._buffer)

    def clear(self) -> None:
        """Clear all buffered messages."""
        with self._lock:
            self._buffer.clear()


class LogBufferHandler(logging.Handler):
    """Logging handler that writes to a LogBuffer.

    This handler captures log messages and adds them to a buffer
    for display in the Live context, preventing log/progress conflicts.
    """

    def __init__(self, log_buffer: LogBuffer):
        """Initialize handler.

        Args:
            log_buffer: LogBuffer to write messages to.
        """
        super().__init__()
        self.log_buffer = log_buffer

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log record to the buffer.

        Args:
            record: Log record to emit.
        """
        try:
            message = self.format(record)
            self.log_buffer.add(message)
        except Exception:
            self.handleError(record)


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
        # Use stderr for Console to align with logging conventions
        self.console = console or Console(stderr=True)

        # Log buffer for integrated display
        self.log_buffer = LogBuffer(max_lines=15)

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
        """Build compact live display panel with metrics and active tasks.

        Returns:
            Panel: Rich panel with transaction status.
        """
        with self._lock:
            # Build compact metrics as single line
            metrics_parts = []
            if self._metrics:
                metrics_parts.append(
                    f"[cyan]Nodes:[/cyan] [magenta]{self._metrics.node_count:,}[/magenta]"
                )
                metrics_parts.append(
                    f"[cyan]Edges:[/cyan] [magenta]{self._metrics.edge_count:,}[/magenta]"
                )
                metrics_parts.append(
                    f"[cyan]Time:[/cyan] [blue]{self._metrics.elapsed:.1f}s[/blue]"
                )

            metrics_line = (
                "  ".join(metrics_parts)
                if metrics_parts
                else "[dim]Initializing...[/dim]"
            )

            # Build active tasks (compact, max 3)
            active_tasks_text = Text()
            if self._active_tasks:
                tasks = list(self._active_tasks)[:3]  # Show max 3
                for task in tasks:
                    active_tasks_text.append(f"→ {task}\n", style="dim cyan")
                if len(self._active_tasks) > 3:
                    active_tasks_text.append(
                        f"... and {len(self._active_tasks) - 3} more",
                        style="dim yellow",
                    )
            else:
                active_tasks_text.append("(no active tasks)", style="dim")

            # Combine into compact group
            group = Group(
                Text(metrics_line, justify="left"),
                Text(),  # Empty line
                active_tasks_text,
            )

            return Panel(
                group,
                title="[bold yellow]Progress[/bold yellow]",
                border_style="yellow",
                padding=(0, 1),  # Minimal padding
            )

    def _update_live_display(self) -> None:
        """Background thread function to update live display."""
        while not self._stop_event.is_set():
            try:
                if self._live and self._progress:
                    # Build complete display: logs + progress + metrics
                    with self._lock:
                        # Get recent log lines
                        log_lines = self.log_buffer.get_lines()
                        log_text = Text()
                        if log_lines:
                            for line in log_lines[-10:]:  # Show last 10 lines
                                log_text.append(line + "\n")
                        else:
                            log_text.append("[dim]No recent logs[/dim]\n")

                        # Build complete renderable with logs on top
                        renderable = Group(
                            Panel(
                                log_text,
                                title="[bold cyan]Recent Logs[/bold cyan]",
                                border_style="cyan",
                                padding=(0, 1),
                            ),
                            self._progress,
                            self._build_live_panel(),
                        )
                    self._live.update(renderable)
            except Exception as e:
                logger.debug("Live display update error: %s", e)

            # Sleep with early exit on stop event
            self._stop_event.wait(0.25)

    @contextmanager
    def live_display(self):
        """Context manager for live progress display.

        Progress bar is displayed at the bottom while logs scroll above.

        Yields:
            ProgressManager: Self for chaining.
        """
        if not self.enabled or not self._progress:
            yield self
            return

        try:
            if self.live_panel_enabled:
                # Create initial display with log area
                initial_renderable = Group(
                    Panel(
                        Text("[dim]No recent logs[/dim]"),
                        title="[bold cyan]Recent Logs[/bold cyan]",
                        border_style="cyan",
                        padding=(0, 1),
                    ),
                    self._progress,
                    self._build_live_panel(),
                )

                # Use Live with vertical overflow to allow content to scroll
                with Live(
                    initial_renderable,
                    console=self.console,
                    refresh_per_second=4,
                    transient=False,  # Keep display after exit
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
            logger.error("Progress display error: %s", e)
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
