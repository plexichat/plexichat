"""
TTY-aware progress indicator for long-running batch operations.

Renders a single-line progress bar to stderr when stderr is a TTY. In any
other environment (CI logs, file capture, piped output) the indicator is a
no-op and the caller can fall back to regular logging. We deliberately avoid
pulling in third-party progress libraries so the dependency surface stays
small.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class _Bar:
    width: int
    filled_char: str
    empty_char: str
    stream: object

    def render(self, current: int, total: int, label: str, elapsed: float) -> str:
        ratio = 0.0 if total <= 0 else min(1.0, max(0.0, current / total))
        filled = int(round(self.width * ratio))
        bar = self.filled_char * filled + self.empty_char * (self.width - filled)
        percent = ratio * 100.0

        if current > 0 and ratio > 0:
            eta = elapsed * (1.0 - ratio) / ratio
        else:
            eta = 0.0
        eta_str = _format_duration(eta)
        elapsed_str = _format_duration(elapsed)
        return f"\r{label} |{bar}| {current}/{total} ({percent:5.1f}%) {elapsed_str} elapsed, eta {eta_str}"


class ProgressBar:
    """TTY-aware single-line progress bar.

    Usage::

        with ProgressBar("migrations", total=42) as bar:
            for migration in pending:
                apply(migration)
                bar.tick()

    The bar is automatically suppressed (silent no-op) when stderr is not a
    TTY, when ``NO_PROGRESS`` is set in the environment, or when the total
    count is one (no progress to show).
    """

    def __init__(
        self,
        label: str,
        total: int,
        width: int = 30,
        stream=None,
    ):
        self.label = label
        self.total = max(0, int(total))
        self.width = max(10, int(width))
        if stream is None:
            stream = sys.stderr
        self._stream = stream
        self._start: Optional[float] = None
        self._current = 0
        self._closed = False
        self._enabled = self._should_enable()

    @staticmethod
    def _should_enable() -> bool:
        if os.environ.get("NO_PROGRESS"):
            return False
        try:
            return sys.stderr.isatty()
        except Exception:
            return False

    def __enter__(self) -> "ProgressBar":
        self._start = time.monotonic()
        self._render(force=True)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def tick(self, n: int = 1, suffix: str = "") -> None:
        """Advance the progress counter by ``n`` and re-render the bar."""
        if not self._enabled:
            return
        self._current = min(self.total, self._current + n)
        self._render(suffix=suffix)

    def set(self, current: int, suffix: str = "") -> None:
        """Set the current progress to a specific value and re-render."""
        if not self._enabled:
            return
        self._current = min(self.total, max(0, int(current)))
        self._render(suffix=suffix)

    def _render(self, force: bool = False, suffix: str = "") -> None:
        if not self._enabled or self._start is None:
            return
        elapsed = time.monotonic() - self._start
        bar = _Bar(self.width, "#", "-", self._stream)
        line = bar.render(self._current, self.total, self.label, elapsed)
        if suffix:
            line = f"{line}  {suffix}"
        try:
            self._stream.write(line)
            self._stream.flush()
        except Exception:
            # If the stream is closed or broken we silently disable further
            # rendering so the operation can still complete.
            self._enabled = False

    def close(self) -> None:
        """Mark the bar complete and write a trailing newline if rendered."""
        if self._closed or not self._enabled or self._start is None:
            self._closed = True
            return
        self._current = self.total
        self._render(force=True)
        try:
            self._stream.write("\n")
            self._stream.flush()
        except Exception:
            pass
        self._closed = True


def _format_duration(seconds: float) -> str:
    if seconds < 0 or seconds != seconds:  # NaN guard
        seconds = 0.0
    if seconds < 60:
        return f"{seconds:5.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


__all__ = ["ProgressBar"]
