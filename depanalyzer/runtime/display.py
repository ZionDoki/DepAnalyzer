"""Rich-based display manager for transaction execution.

This module provides a unified display system using Rich library:
- Per-package progress tracking (each package has 7 phases)
- Statistics collection (nodes, edges, files, depth, etc.)
- Optional file logging

Usage:
    display = RichDisplayManager(enabled=True, log_file="output.log")
    with display.live_display():
        display.register_package("lodash", "4.17.21", depth=1)
        display.start_package_phase("lodash@4.17.21", LifecyclePhase.ACQUIRE)
        # ... do work ...
        display.complete_package_phase("lodash@4.17.21", LifecyclePhase.ACQUIRE)
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Generator, List, Optional

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from depanalyzer.runtime.lifecycle import LifecyclePhase

logger = logging.getLogger("depanalyzer.runtime.display")


class PhaseStatus(Enum):
    """Status of a lifecycle phase."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# Status icons and colors
_STATUS_ICONS = {
    PhaseStatus.PENDING: ("◌", "dim"),
    PhaseStatus.RUNNING: ("⟳", "cyan"),
    PhaseStatus.COMPLETED: ("✓", "green"),
    PhaseStatus.FAILED: ("✗", "red"),
    PhaseStatus.SKIPPED: ("⊘", "yellow"),
}

# Phase abbreviations for compact display
_PHASE_ABBREV = {
    LifecyclePhase.ACQUIRE: "ACQ",
    LifecyclePhase.DETECT: "DET",
    LifecyclePhase.PARSE: "PAR",
    LifecyclePhase.RESOLVE_DEPS: "RES",
    LifecyclePhase.JOIN: "JOI",
    LifecyclePhase.ANALYZE: "ANA",
    LifecyclePhase.EXPORT: "EXP",
}


@dataclass
class TrackedPackage:
    """Per-package progress tracking with phase-level detail."""

    name: str
    version: str
    depth: int
    # Per-phase status
    phase_status: Dict[LifecyclePhase, PhaseStatus] = field(default_factory=dict)
    current_phase: Optional[LifecyclePhase] = None
    start_time: float = 0.0
    end_time: Optional[float] = None
    error: Optional[str] = None
    # Statistics
    node_count: int = 0
    edge_count: int = 0
    file_count: int = 0

    def __post_init__(self) -> None:
        """Initialize all phases as PENDING."""
        if not self.phase_status:
            for phase in LifecyclePhase:
                self.phase_status[phase] = PhaseStatus.PENDING

    @property
    def key(self) -> str:
        """Get unique key for this package."""
        return f"{self.name}@{self.version}" if self.version else self.name

    @property
    def elapsed(self) -> float:
        """Get elapsed time for this package."""
        if self.start_time == 0:
            return 0.0
        end = self.end_time or time.time()
        return end - self.start_time

    @property
    def is_completed(self) -> bool:
        """Check if all phases are completed."""
        return all(
            s in (PhaseStatus.COMPLETED, PhaseStatus.SKIPPED)
            for s in self.phase_status.values()
        )

    @property
    def is_failed(self) -> bool:
        """Check if any phase failed."""
        return any(s == PhaseStatus.FAILED for s in self.phase_status.values())

    @property
    def is_running(self) -> bool:
        """Check if any phase is running."""
        return any(s == PhaseStatus.RUNNING for s in self.phase_status.values())


@dataclass
class DisplayStats:
    """Statistics for display."""

    node_count: int = 0
    edge_count: int = 0
    files_total: int = 0
    files_processed: int = 0
    files_per_second: float = 0.0
    third_party_count: int = 0
    # Recursion depth statistics
    max_recursion_depth: int = 0
    avg_recursion_depth: float = 0.0
    total_child_transactions: int = 0
    elapsed_seconds: float = 0.0
    # Package counts
    packages_total: int = 0
    packages_completed: int = 0
    packages_failed: int = 0


class PackageTracker:
    """Thread-safe tracker for all packages."""

    def __init__(self) -> None:
        """Initialize package tracker."""
        self._packages: Dict[str, TrackedPackage] = {}
        self._lock = threading.RLock()

    def register_package(
        self, name: str, version: str = "", depth: int = 1
    ) -> TrackedPackage:
        """Register a new package for tracking."""
        with self._lock:
            pkg = TrackedPackage(name=name, version=version, depth=depth)
            pkg.start_time = time.time()
            self._packages[pkg.key] = pkg
            return pkg

    def get_package(self, key: str) -> Optional[TrackedPackage]:
        """Get a package by key."""
        with self._lock:
            return self._packages.get(key)

    def start_phase(self, pkg_key: str, phase: LifecyclePhase) -> None:
        """Start a phase for a package."""
        with self._lock:
            pkg = self._packages.get(pkg_key)
            if pkg:
                pkg.phase_status[phase] = PhaseStatus.RUNNING
                pkg.current_phase = phase

    def complete_phase(self, pkg_key: str, phase: LifecyclePhase) -> None:
        """Complete a phase for a package."""
        with self._lock:
            pkg = self._packages.get(pkg_key)
            if pkg:
                pkg.phase_status[phase] = PhaseStatus.COMPLETED
                # Check if all phases completed
                if pkg.is_completed:
                    pkg.end_time = time.time()
                    pkg.current_phase = None

    def fail_package(self, pkg_key: str, error: str) -> None:
        """Mark a package as failed."""
        with self._lock:
            pkg = self._packages.get(pkg_key)
            if pkg:
                if pkg.current_phase:
                    pkg.phase_status[pkg.current_phase] = PhaseStatus.FAILED
                pkg.error = error
                pkg.end_time = time.time()

    def update_package_stats(
        self, pkg_key: str, node_count: int = 0, edge_count: int = 0, file_count: int = 0
    ) -> None:
        """Update package statistics."""
        with self._lock:
            pkg = self._packages.get(pkg_key)
            if pkg:
                pkg.node_count = node_count
                pkg.edge_count = edge_count
                pkg.file_count = file_count

    def get_packages_sorted(self) -> List[TrackedPackage]:
        """Get packages sorted by priority: running > pending (oldest first) > completed."""
        with self._lock:
            packages = list(self._packages.values())

        def sort_key(pkg: TrackedPackage) -> tuple:
            # Priority: running (0) > pending (1) > failed (2) > completed (3)
            # Within each category:
            # - running: oldest first (likely to complete soon)
            # - pending: oldest first (likely to start soon)
            # - failed/completed: newest first (most recent results)
            if pkg.is_running:
                return (0, pkg.start_time)  # oldest running first
            elif not pkg.is_completed and not pkg.is_failed:
                return (1, pkg.start_time)  # oldest pending first
            elif pkg.is_failed:
                return (2, -pkg.start_time)  # newest failed first
            else:
                return (3, -pkg.start_time)  # newest completed first

        return sorted(packages, key=sort_key)

    def get_counts(self) -> tuple:
        """Get (total, completed, failed) counts."""
        with self._lock:
            total = len(self._packages)
            completed = sum(1 for p in self._packages.values() if p.is_completed)
            failed = sum(1 for p in self._packages.values() if p.is_failed)
            return total, completed, failed


class StatsCollector:
    """Low-cost statistics collector."""

    def __init__(self) -> None:
        """Initialize stats collector."""
        self._stats = DisplayStats()
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._file_timestamps: List[float] = []
        self._recursion_depths: List[int] = []

    def update_graph_stats(self, node_count: int, edge_count: int) -> None:
        """Update graph node/edge counts."""
        with self._lock:
            self._stats.node_count = node_count
            self._stats.edge_count = edge_count

    def add_graph_stats(self, node_count: int, edge_count: int) -> None:
        """Add to graph node/edge counts (for aggregation)."""
        with self._lock:
            self._stats.node_count += node_count
            self._stats.edge_count += edge_count

    def set_files_total(self, total: int) -> None:
        """Set total files to process."""
        with self._lock:
            self._stats.files_total = total

    def add_files_total(self, count: int) -> None:
        """Add to total files count."""
        with self._lock:
            self._stats.files_total += count

    def record_file_processed(self) -> None:
        """Record a file has been processed."""
        with self._lock:
            self._stats.files_processed += 1
            self._file_timestamps.append(time.time())
            # Keep only last 100 timestamps for speed calculation
            if len(self._file_timestamps) > 100:
                self._file_timestamps = self._file_timestamps[-100:]

    def increment_third_party(self) -> None:
        """Increment third-party dependency count."""
        with self._lock:
            self._stats.third_party_count += 1

    def record_child_transaction(self, depth: int) -> None:
        """Record a child transaction with its recursion depth."""
        with self._lock:
            self._stats.total_child_transactions += 1
            self._recursion_depths.append(depth)
            self._stats.max_recursion_depth = max(
                self._stats.max_recursion_depth, depth
            )

    def get_stats(self) -> DisplayStats:
        """Get current statistics (computes derived values)."""
        with self._lock:
            stats = DisplayStats(
                node_count=self._stats.node_count,
                edge_count=self._stats.edge_count,
                files_total=self._stats.files_total,
                files_processed=self._stats.files_processed,
                third_party_count=self._stats.third_party_count,
                max_recursion_depth=self._stats.max_recursion_depth,
                total_child_transactions=self._stats.total_child_transactions,
                elapsed_seconds=time.time() - self._start_time,
            )

            # Calculate files per second
            if len(self._file_timestamps) >= 2:
                duration = self._file_timestamps[-1] - self._file_timestamps[0]
                if duration > 0:
                    stats.files_per_second = len(self._file_timestamps) / duration

            # Calculate average recursion depth
            if self._recursion_depths:
                stats.avg_recursion_depth = sum(self._recursion_depths) / len(
                    self._recursion_depths
                )

            return stats


class RichDisplayManager:
    """Rich-based display manager for transaction execution.

    Features:
    - Per-package progress tracking (each package has 7 phases)
    - Statistics panel
    - Optional file logging
    - Thread-safe for concurrent updates
    """

    def __init__(
        self,
        enabled: bool = True,
        console: Optional[Console] = None,
        log_file: Optional[str] = None,
    ) -> None:
        """Initialize display manager."""
        self.enabled = enabled
        self.console = console or Console(stderr=True)
        self.log_file = log_file

        # Core components
        self.package_tracker = PackageTracker()
        self.stats_collector = StatsCollector()

        # Rich components
        self._live: Optional[Live] = None
        self._update_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Transaction info
        self._graph_id: str = ""
        self._source: str = ""

        # File logging
        self._file_handler: Optional[logging.FileHandler] = None
        if log_file:
            self._setup_file_logging(log_file)

    def _setup_file_logging(self, log_file: str) -> None:
        """Set up file logging handler."""
        try:
            self._file_handler = logging.FileHandler(log_file, encoding="utf-8")
            self._file_handler.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
            )
            logging.getLogger("depanalyzer").addHandler(self._file_handler)
            logger.info("File logging enabled: %s", log_file)
        except OSError as e:
            logger.warning("Failed to set up file logging: %s", e)

    def start_transaction(self, graph_id: str, source: str) -> None:
        """Start transaction tracking."""
        self._graph_id = graph_id
        self._source = source

    # ===== Package-level tracking =====

    def register_package(
        self, name: str, version: str = "", depth: int = 1
    ) -> str:
        """Register a new package for tracking.

        Returns:
            Package key for subsequent calls.
        """
        if not self.enabled:
            return f"{name}@{version}" if version else name

        pkg = self.package_tracker.register_package(name, version, depth)
        self.stats_collector.increment_third_party()
        self.stats_collector.record_child_transaction(depth)
        return pkg.key

    def start_package_phase(self, pkg_key: str, phase: LifecyclePhase) -> None:
        """Start a phase for a package."""
        if not self.enabled:
            return
        self.package_tracker.start_phase(pkg_key, phase)

    def complete_package_phase(self, pkg_key: str, phase: LifecyclePhase) -> None:
        """Complete a phase for a package."""
        if not self.enabled:
            return
        self.package_tracker.complete_phase(pkg_key, phase)

    def complete_package(
        self,
        pkg_key: str,
        node_count: int = 0,
        edge_count: int = 0,
        file_count: int = 0,
    ) -> None:
        """Mark package as fully completed with stats."""
        if not self.enabled:
            return

        self.package_tracker.update_package_stats(
            pkg_key, node_count, edge_count, file_count
        )
        # Aggregate stats
        self.stats_collector.add_graph_stats(node_count, edge_count)

    def fail_package(self, pkg_key: str, error: str) -> None:
        """Mark a package as failed."""
        if not self.enabled:
            return
        self.package_tracker.fail_package(pkg_key, error)

    # ===== Legacy compatibility methods =====

    def add_dependency(self, name: str, depth: int, version: str = "") -> None:
        """Add a dependency (legacy compatibility).

        Args:
            name: Dependency name.
            depth: Recursion depth for display grouping.
            version: Optional dependency version used for the package key.

        Returns:
            None
        """
        if not self.enabled:
            return
        self.register_package(name, version, depth)

    def start_phase(self, phase: LifecyclePhase, description: str = "") -> str:
        """Start tracking a lifecycle phase (legacy compatibility)."""
        return f"phase_{phase.name}"

    def update_phase(
        self,
        phase: LifecyclePhase,
        progress: Optional[float] = None,
        current_item: Optional[str] = None,
    ) -> None:
        """Update phase progress (legacy compatibility - no-op)."""
        pass

    def complete_phase(self, phase_id: str) -> None:
        """Mark phase as complete (legacy compatibility - no-op)."""
        pass

    def fail_phase(self, phase_id: str, error: str) -> None:
        """Mark phase as failed (legacy compatibility - no-op)."""
        pass

    def update_metrics(
        self,
        node_count: Optional[int] = None,
        edge_count: Optional[int] = None,
    ) -> None:
        """Update graph metrics."""
        if not self.enabled:
            return

        if node_count is not None and edge_count is not None:
            self.stats_collector.update_graph_stats(node_count, edge_count)

    def add_active_task(self, task_name: str) -> None:
        """Compatibility: Add active task (no-op)."""
        pass

    def remove_active_task(self, task_name: str) -> None:
        """Compatibility: Remove active task (no-op)."""
        pass

    # ===== Display building =====

    def _build_phase_line(self, pkg: TrackedPackage, compact: bool = False) -> Text:
        """Build phase progress line for a package."""
        line = Text()

        for i, phase in enumerate(LifecyclePhase):
            status = pkg.phase_status.get(phase, PhaseStatus.PENDING)
            icon, color = _STATUS_ICONS[status]
            abbrev = _PHASE_ABBREV[phase]

            if compact:
                # Compact: just icons
                line.append(icon, style=color)
            else:
                line.append(icon, style=color)
                line.append(f" {abbrev}", style=color if status != PhaseStatus.PENDING else "dim")

            # Add arrow between phases
            if i < len(LifecyclePhase) - 1:
                if compact:
                    line.append("→", style="dim")
                else:
                    line.append(" → ", style="dim")

        return line

    def _get_available_lines(self) -> int:
        """Get available lines for package display based on terminal height."""
        try:
            term_height = self.console.size.height
        except Exception:
            term_height = 24  # Default fallback

        # Reserve lines for: border(2) + main(3) + deps_header(1) + recent_header(1)
        # + stats(1) + padding(2) + extra margin(4)
        reserved = 14
        # Use only 60% of remaining space to leave room
        available = max(3, int((term_height - reserved) * 0.6))
        return available

    def _build_display(self) -> Panel:
        """Build the display panel."""
        packages = self.package_tracker.get_packages_sorted()
        stats = self.stats_collector.get_stats()
        total, completed, failed = self.package_tracker.get_counts()

        content_parts = []

        # Separate main project (depth=0) from dependencies
        main_pkg = None
        dep_packages = []
        for pkg in packages:
            if pkg.depth == 0:
                main_pkg = pkg
            else:
                dep_packages.append(pkg)

        # Calculate available lines for display
        available_lines = self._get_available_lines()

        # Allocate lines: 60% for running/pending, 40% for recent completed
        lines_for_active = max(2, int(available_lines * 0.6))
        lines_for_recent = max(2, available_lines - lines_for_active)

        # === Main Project Section ===
        if main_pkg:
            main_header = Text("Main: ", style="bold")
            main_header.append(main_pkg.name, style="bold cyan")
            if main_pkg.is_completed:
                main_header.append(f" ✓ [{main_pkg.elapsed:.1f}s]", style="green")
            content_parts.append(main_header)

            phase_line = Text("  ")
            phase_line.append_text(self._build_phase_line(main_pkg))
            content_parts.append(phase_line)
            content_parts.append(Text())

        # === Dependencies Section ===
        if dep_packages:
            # Count by status
            running = [p for p in dep_packages if p.is_running]
            pending = [p for p in dep_packages if not p.is_completed and not p.is_failed and not p.is_running]
            done = [p for p in dep_packages if p.is_completed]
            failed_pkgs = [p for p in dep_packages if p.is_failed]

            # Sort done by end_time descending (most recent first)
            done_sorted = sorted(done, key=lambda p: p.end_time or 0, reverse=True)

            dep_header = Text(f"Dependencies: ", style="bold")
            dep_header.append(f"{len(running)} running", style="cyan")
            dep_header.append(" │ ", style="dim")
            dep_header.append(f"{len(pending)} pending", style="yellow")
            dep_header.append(" │ ", style="dim")
            dep_header.append(f"{len(done)} done", style="green")
            if failed_pkgs:
                dep_header.append(" │ ", style="dim")
                dep_header.append(f"{len(failed_pkgs)} failed", style="red")
            content_parts.append(dep_header)

            # Show running packages first
            shown = 0
            max_show = lines_for_active

            for pkg in running[:max_show - shown]:
                pkg_line = Text("  ⟳ ", style="cyan")
                pkg_line.append(pkg.name, style="cyan")
                if pkg.version:
                    pkg_line.append(f"@{pkg.version}", style="dim cyan")
                pkg_line.append(f" (d:{pkg.depth}) ", style="dim")
                pkg_line.append_text(self._build_phase_line(pkg, compact=True))
                content_parts.append(pkg_line)
                shown += 1

            # Show pending packages (fill remaining slots)
            for pkg in pending[:max_show - shown]:
                pkg_line = Text("  ◌ ", style="dim")
                pkg_line.append(pkg.name, style="dim")
                if pkg.version:
                    pkg_line.append(f"@{pkg.version}", style="dim")
                pkg_line.append(f" (d:{pkg.depth})", style="dim")
                content_parts.append(pkg_line)
                shown += 1

            # Show summary of remaining active
            remaining_active = len(running) + len(pending) - shown
            if remaining_active > 0:
                remaining_text = Text(f"  ... +{remaining_active} more", style="dim")
                content_parts.append(remaining_text)

            content_parts.append(Text())

            # === Recently Completed Section ===
            if done_sorted:
                recent_header = Text("Recently Completed: ", style="bold green")
                content_parts.append(recent_header)

                for pkg in done_sorted[:lines_for_recent]:
                    pkg_line = Text("  ✓ ", style="green")
                    pkg_line.append(pkg.name, style="green")
                    if pkg.version:
                        pkg_line.append(f"@{pkg.version}", style="dim green")
                    pkg_line.append(f" (d:{pkg.depth})", style="dim")
                    pkg_line.append(f" [{pkg.elapsed:.1f}s]", style="dim green")
                    content_parts.append(pkg_line)

                remaining_done = len(done_sorted) - lines_for_recent
                if remaining_done > 0:
                    remaining_text = Text(f"  ... +{remaining_done} more completed", style="dim green")
                    content_parts.append(remaining_text)

                content_parts.append(Text())

        # === Stats Section ===
        stats_line = Text()
        stats_line.append(f"Nodes: {stats.node_count:,}", style="cyan")
        stats_line.append(" │ ", style="dim")
        stats_line.append(f"Edges: {stats.edge_count:,}", style="cyan")
        stats_line.append(" │ ", style="dim")
        stats_line.append(f"Files: {stats.files_processed}", style="magenta")
        stats_line.append(" │ ", style="dim")
        stats_line.append(f"Speed: {stats.files_per_second:.1f}/s", style="blue")
        stats_line.append(" │ ", style="dim")
        stats_line.append(f"Depth: {stats.max_recursion_depth}", style="yellow")
        stats_line.append(" │ ", style="dim")

        # Format elapsed time
        elapsed = int(stats.elapsed_seconds)
        minutes, seconds = divmod(elapsed, 60)
        stats_line.append(f"{minutes:02d}:{seconds:02d}", style="dim")
        content_parts.append(stats_line)

        return Panel(
            Group(*content_parts),
            title=Text("Depanalyzer Progress", style="bold yellow"),
            border_style="yellow",
            padding=(0, 1),
        )

    def _update_loop(self) -> None:
        """Background thread for updating display."""
        while not self._stop_event.is_set():
            try:
                if self._live:
                    self._live.update(self._build_display())
            except (RuntimeError, ValueError, TypeError, AttributeError, OSError) as e:
                logger.debug("Display update error: %s", e)

            self._stop_event.wait(0.25)

    @contextmanager
    def live_display(self) -> Generator["RichDisplayManager", None, None]:
        """Context manager for live progress display.

        During live display, INFO level logs are suppressed to avoid
        interference with the Rich panel. WARNING and above are still shown.
        File logging (if enabled via --log-file) is not affected.
        """
        if not self.enabled:
            yield self
            return

        # Raise log level to WARNING for non-file handlers during live display
        # This hides INFO but shows WARNING/ERROR
        root_logger = logging.getLogger()
        depanalyzer_logger = logging.getLogger("depanalyzer")

        # Save original levels for non-file handlers
        original_levels: List[tuple] = []
        for handler in root_logger.handlers:
            if not isinstance(handler, logging.FileHandler):
                original_levels.append((handler, handler.level))
                handler.setLevel(logging.WARNING)
        for handler in depanalyzer_logger.handlers:
            if not isinstance(handler, logging.FileHandler):
                original_levels.append((handler, handler.level))
                handler.setLevel(logging.WARNING)

        try:
            self._live = Live(
                self._build_display(),
                console=self.console,
                refresh_per_second=4,
                transient=False,
            )
            self._live.start()

            self._stop_event.clear()
            self._update_thread = threading.Thread(
                target=self._update_loop,
                daemon=True,
            )
            self._update_thread.start()

            yield self

        except (RuntimeError, ValueError, TypeError, AttributeError, OSError) as e:
            logger.error("Display error: %s", e)
            yield self

        finally:
            self._stop_event.set()
            if self._update_thread and self._update_thread.is_alive():
                self._update_thread.join(timeout=1.0)
            if self._live:
                try:
                    self._live.stop()
                except (RuntimeError, ValueError, AttributeError):
                    pass
                self._live = None
            self._update_thread = None

            # Restore original log levels
            for handler, level in original_levels:
                handler.setLevel(level)

    def stop(self) -> None:
        """Stop display and cleanup."""
        self._stop_event.set()

        if self._update_thread and self._update_thread.is_alive():
            self._update_thread.join(timeout=1.0)

        if self._live:
            try:
                self._live.stop()
            except (RuntimeError, ValueError, AttributeError):
                pass
            self._live = None

        if self._file_handler:
            try:
                logging.getLogger("depanalyzer").removeHandler(self._file_handler)
                self._file_handler.close()
            except (RuntimeError, ValueError, AttributeError):
                pass
            self._file_handler = None
