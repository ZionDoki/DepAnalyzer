"""Metrics collection for performance monitoring and debugging.

This module provides a lightweight metrics collection system for tracking
counters, timers, and gauges throughout the depanalyzer pipeline.

Usage:
    from depanalyzer.utils.metrics import get_metrics

    metrics = get_metrics()
    metrics.increment("files_parsed")

    with metrics.timer("parse_duration"):
        # ... parsing code ...

    metrics.set_gauge("queue_size", len(queue))

    # Get summary at end of run
    print(metrics.summary())
"""

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger("depanalyzer.metrics")


@dataclass
class TimerStats:
    """Statistics for a timer metric.

    Attributes:
        count: Number of times the timer was invoked.
        total: Total elapsed time in seconds.
        min: Minimum elapsed time.
        max: Maximum elapsed time.
    """

    count: int = 0
    total: float = 0.0
    min: float = float("inf")
    max: float = 0.0

    def record(self, elapsed: float) -> None:
        """Record a new timing measurement."""
        self.count += 1
        self.total += elapsed
        self.min = min(self.min, elapsed)
        self.max = max(self.max, elapsed)

    @property
    def avg(self) -> float:
        """Calculate average elapsed time."""
        return self.total / self.count if self.count > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "count": self.count,
            "total": round(self.total, 3),
            "avg": round(self.avg, 3),
            "min": round(self.min, 3) if self.min != float("inf") else 0.0,
            "max": round(self.max, 3),
        }


class Metrics:
    """Thread-safe metrics collection.

    Provides counters, timers, and gauges for monitoring application performance.
    All operations are thread-safe.
    """

    def __init__(self) -> None:
        """Initialize metrics collection."""
        self._counters: Dict[str, int] = {}
        self._timers: Dict[str, TimerStats] = {}
        self._gauges: Dict[str, float] = {}
        self._lock = threading.RLock()
        self._start_time = time.time()

    def increment(self, name: str, value: int = 1) -> int:
        """Increment a counter.

        Args:
            name: Counter name.
            value: Amount to increment (default 1).

        Returns:
            New counter value.
        """
        with self._lock:
            current = self._counters.get(name, 0)
            new_value = current + value
            self._counters[name] = new_value
            return new_value

    def decrement(self, name: str, value: int = 1) -> int:
        """Decrement a counter.

        Args:
            name: Counter name.
            value: Amount to decrement (default 1).

        Returns:
            New counter value.
        """
        return self.increment(name, -value)

    def get_counter(self, name: str) -> int:
        """Get current counter value.

        Args:
            name: Counter name.

        Returns:
            Current counter value (0 if not set).
        """
        with self._lock:
            return self._counters.get(name, 0)

    @contextmanager
    def timer(self, name: str) -> Generator[None, None, None]:
        """Context manager for timing code blocks.

        Args:
            name: Timer name.

        Yields:
            None

        Example:
            with metrics.timer("parse_file"):
                parse_file(path)
        """
        start = time.time()
        try:
            yield
        finally:
            elapsed = time.time() - start
            self.record_time(name, elapsed)

    def record_time(self, name: str, elapsed: float) -> None:
        """Record a timing measurement.

        Args:
            name: Timer name.
            elapsed: Elapsed time in seconds.
        """
        with self._lock:
            if name not in self._timers:
                self._timers[name] = TimerStats()
            self._timers[name].record(elapsed)

    def get_timer(self, name: str) -> Optional[TimerStats]:
        """Get timer statistics.

        Args:
            name: Timer name.

        Returns:
            TimerStats or None if timer doesn't exist.
        """
        with self._lock:
            return self._timers.get(name)

    def set_gauge(self, name: str, value: float) -> None:
        """Set a gauge value.

        Gauges represent point-in-time values (e.g., queue size, memory usage).

        Args:
            name: Gauge name.
            value: Gauge value.
        """
        with self._lock:
            self._gauges[name] = value

    def get_gauge(self, name: str) -> Optional[float]:
        """Get current gauge value.

        Args:
            name: Gauge name.

        Returns:
            Gauge value or None if not set.
        """
        with self._lock:
            return self._gauges.get(name)

    def summary(self) -> Dict[str, Any]:
        """Get summary of all metrics.

        Returns:
            Dictionary containing all counters, timers, and gauges.
        """
        with self._lock:
            elapsed = time.time() - self._start_time
            return {
                "elapsed_seconds": round(elapsed, 3),
                "counters": dict(self._counters),
                "timers": {name: stats.to_dict() for name, stats in self._timers.items()},
                "gauges": {name: round(value, 3) for name, value in self._gauges.items()},
            }

    def reset(self) -> None:
        """Reset all metrics."""
        with self._lock:
            self._counters.clear()
            self._timers.clear()
            self._gauges.clear()
            self._start_time = time.time()
            logger.debug("Metrics reset")

    def log_summary(self, level: int = logging.INFO) -> None:
        """Log metrics summary.

        Args:
            level: Logging level (default INFO).
        """
        summary = self.summary()
        logger.log(level, "Metrics Summary:")
        logger.log(level, "  Elapsed: %.2fs", summary["elapsed_seconds"])

        if summary["counters"]:
            logger.log(level, "  Counters:")
            for name, value in sorted(summary["counters"].items()):
                logger.log(level, "    %s: %d", name, value)

        if summary["timers"]:
            logger.log(level, "  Timers:")
            for name, stats in sorted(summary["timers"].items()):
                logger.log(
                    level,
                    "    %s: count=%d, avg=%.3fs, total=%.3fs",
                    name,
                    stats["count"],
                    stats["avg"],
                    stats["total"],
                )

        if summary["gauges"]:
            logger.log(level, "  Gauges:")
            for name, value in sorted(summary["gauges"].items()):
                logger.log(level, "    %s: %.3f", name, value)


# Global metrics instance
_metrics: Optional[Metrics] = None
_metrics_lock = threading.Lock()


def get_metrics() -> Metrics:
    """Get the global metrics instance.

    Returns:
        Global Metrics instance.
    """
    global _metrics
    if _metrics is None:
        with _metrics_lock:
            if _metrics is None:
                _metrics = Metrics()
    return _metrics


def reset_metrics() -> None:
    """Reset the global metrics instance."""
    global _metrics
    with _metrics_lock:
        if _metrics is not None:
            _metrics.reset()
        else:
            _metrics = Metrics()
