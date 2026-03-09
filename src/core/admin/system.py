"""
System metrics for Plexichat Admin.
"""

import time
import os
import threading
from typing import Dict, Any

try:
    import psutil  # type: ignore
except ImportError:
    psutil = None  # type: ignore

# Store process start time for uptime calculation
_process_start_time = time.time()


def get_system_metrics() -> Dict[str, Any]:
    """Get real-time system and process health metrics."""
    try:
        if psutil is None:
            return {
                "cpu_percent": 0.0,
                "memory_percent": 0.0,
                "memory_used_mb": 0.0,
                "memory_total_mb": 0.0,
                "disk_percent": 0.0,
                "process_memory_mb": 0.0,
                "thread_count": 0,
                "uptime_seconds": 0.0,
            }

        # System-wide metrics
        cpu_percent = psutil.cpu_percent(interval=None)
        virtual_mem = psutil.virtual_memory()
        disk_usage = psutil.disk_usage("/")

        # Process-specific metrics
        process = psutil.Process(os.getpid())
        process_mem = process.memory_info().rss / (1024 * 1024)  # MB
        thread_count = threading.active_count()
        uptime = time.time() - _process_start_time

        return {
            "cpu_percent": cpu_percent,
            "memory_percent": virtual_mem.percent,
            "memory_used_mb": (virtual_mem.total - virtual_mem.available)
            / (1024 * 1024),
            "memory_total_mb": virtual_mem.total / (1024 * 1024),
            "disk_percent": disk_usage.percent,
            "process_memory_mb": process_mem,
            "thread_count": thread_count,
            "uptime_seconds": uptime,
        }
    except Exception as e:
        import utils.logger as logger

        logger.error(f"Failed to collect system metrics: {e}")
        return {
            "cpu_percent": 0.0,
            "memory_percent": 0.0,
            "memory_used_mb": 0.0,
            "memory_total_mb": 0.0,
            "disk_percent": 0.0,
            "process_memory_mb": 0.0,
            "thread_count": 0,
            "uptime_seconds": 0.0,
        }
