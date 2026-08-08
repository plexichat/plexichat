"""
RAM usage monitoring for the self-test runner.

Samples process and system memory. Prefers ``psutil`` (a pinned
dependency) and falls back to ``/proc`` on Linux when unavailable so
the self-test remains usable as a diagnostic tool.
"""

import threading

try:
    import psutil  # type: ignore
except ImportError:
    psutil = None  # type: ignore


def _read_proc_status_rss_mb() -> float:
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0  # kB -> MB
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def _read_proc_meminfo_mb() -> tuple:
    total = used = 0.0
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                parts = line.split()
                if parts[0] == "MemTotal:":
                    total = int(parts[1]) / 1024.0
                elif parts[0] == "MemAvailable:":
                    used = max(total - int(parts[1]) / 1024.0, 0.0)
    except (OSError, ValueError, IndexError):
        pass
    return total, used


def sample_ram() -> dict:
    """Return a snapshot of process and system RAM usage (MB / percent)."""
    if psutil is not None:
        process_rss_mb = psutil.Process().memory_info().rss / (1024 * 1024)
        vmem = psutil.virtual_memory()
        total_mb = vmem.total / (1024 * 1024)
        used_mb = (vmem.total - vmem.available) / (1024 * 1024)
    else:
        process_rss_mb = _read_proc_status_rss_mb()
        total_mb, used_mb = _read_proc_meminfo_mb()

    percent = (used_mb / total_mb * 100.0) if total_mb else 0.0
    return {
        "process_rss_mb": process_rss_mb,
        "system_total_mb": total_mb,
        "system_used_mb": used_mb,
        "system_percent": percent,
    }


class RamMonitor:
    """Samples process RSS over time and reports start/end/peak usage."""

    def __init__(self, interval: float = 0.2):
        self._interval = interval
        self._peak_rss_mb = 0.0
        self._start: dict | None = None
        self._end: dict | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def peak_rss_mb(self) -> float:
        with self._lock:
            return self._peak_rss_mb

    def start(self) -> None:
        self._start = sample_ram()
        with self._lock:
            self._peak_rss_mb = self._start["process_rss_mb"]
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll, name="selftest-ram-monitor", daemon=True
        )
        self._thread.start()

    def _poll(self) -> None:
        while not self._stop_event.wait(self._interval):
            rss = sample_ram()["process_rss_mb"]
            with self._lock:
                if rss > self._peak_rss_mb:
                    self._peak_rss_mb = rss

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=1.0)
        self._thread = None
        self._end = sample_ram()
        with self._lock:
            if self._end["process_rss_mb"] > self._peak_rss_mb:
                self._peak_rss_mb = self._end["process_rss_mb"]

    def summary(self) -> dict | None:
        if self._start is None:
            return None
        end = self._end if self._end is not None else sample_ram()
        return {
            "start_rss_mb": self._start["process_rss_mb"],
            "end_rss_mb": end["process_rss_mb"],
            "peak_rss_mb": self.peak_rss_mb,
            "delta_rss_mb": end["process_rss_mb"] - self._start["process_rss_mb"],
            "system_total_mb": end["system_total_mb"],
            "system_used_mb": end["system_used_mb"],
            "system_percent": end["system_percent"],
        }
