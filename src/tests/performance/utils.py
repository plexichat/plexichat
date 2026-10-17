"""
Performance test utilities and helpers.

Provides common utilities for performance testing including:
- Statistics calculation
- Result formatters
- Load generators
- Data generators
"""

import time
import statistics
from typing import List, Callable, Any, Dict


class PerformanceTimer:
    """Context manager for timing operations."""

    def __init__(self):
        self.start_time = None
        self.end_time = None
        self.elapsed = None

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end_time = time.time()
        self.elapsed = self.end_time - self.start_time

    def get_elapsed(self):
        return self.elapsed


class PerformanceStats:
    """Calculate and format performance statistics."""

    def __init__(self, times: List[float]):
        self.times = times
        self.mean = statistics.mean(times)
        self.median = statistics.median(times)
        self.stdev = statistics.stdev(times) if len(times) > 1 else 0
        self.min = min(times)
        self.max = max(times)
        self.p95 = self._percentile(times, 95)
        self.p99 = self._percentile(times, 99)

    @staticmethod
    def _percentile(data: List[float], percentile: int) -> float:
        """Calculate percentile of a list."""
        sorted_data = sorted(data)
        index = (len(sorted_data) - 1) * percentile / 100
        floor = int(index)
        ceil = floor + 1

        if ceil >= len(sorted_data):
            return sorted_data[floor]

        fraction = index - floor
        return sorted_data[floor] * (1 - fraction) + sorted_data[ceil] * fraction

    def to_dict(self) -> Dict[str, float]:
        return {
            "mean": self.mean,
            "median": self.median,
            "stdev": self.stdev,
            "min": self.min,
            "max": self.max,
            "p95": self.p95,
            "p99": self.p99,
        }

    def __str__(self):
        return (
            f"Mean: {self.mean:.4f}s, Median: {self.median:.4f}s, "
            f"StdDev: {self.stdev:.4f}s, P95: {self.p95:.4f}s, P99: {self.p99:.4f}s"
        )


def measure_throughput(
    operation: Callable, duration_seconds: float = 1.0
) -> Dict[str, Any]:
    """
    Measure throughput of an operation over a time period.

    Args:
        operation: Callable to execute repeatedly
        duration_seconds: How long to run the test

    Returns:
        Dictionary with throughput metrics
    """
    start = time.time()
    count = 0
    times = []

    while time.time() - start < duration_seconds:
        op_start = time.time()
        operation()
        op_elapsed = time.time() - op_start
        times.append(op_elapsed)
        count += 1

    total_elapsed = time.time() - start
    throughput = count / total_elapsed

    return {
        "count": count,
        "duration": total_elapsed,
        "throughput": throughput,
        "stats": PerformanceStats(times).to_dict() if times else {},
    }


def measure_latency(operation: Callable, iterations: int = 100) -> PerformanceStats:
    """
    Measure latency of an operation over multiple iterations.

    Args:
        operation: Callable to execute
        iterations: Number of times to execute

    Returns:
        PerformanceStats object with latency metrics
    """
    times = []

    for _ in range(iterations):
        start = time.time()
        operation()
        elapsed = time.time() - start
        times.append(elapsed)

    return PerformanceStats(times)


def generate_test_messages(count: int, prefix: str = "Test message") -> List[str]:
    """Generate test message content."""
    return [f"{prefix} {i}" for i in range(count)]


def generate_test_users(count: int, prefix: str = "perfuser") -> List[Dict[str, str]]:
    """Generate test user data."""
    return [
        {
            "username": f"{prefix}_{i}",
            "email": f"{prefix}_{i}@example.com",
            "password": "PerformanceTest123!@#",
        }
        for i in range(count)
    ]


class LoadPattern:
    """Generate different load patterns for testing."""

    @staticmethod
    def constant(rate: int, duration: float) -> List[float]:
        """Constant load pattern."""
        interval = 1.0 / rate
        num_operations = int(rate * duration)
        return [i * interval for i in range(num_operations)]

    @staticmethod
    def ramp_up(
        start_rate: int, end_rate: int, duration: float, steps: int = 10
    ) -> List[float]:
        """Ramping load pattern."""
        times = []
        step_duration = duration / steps

        for step in range(steps):
            rate = start_rate + (end_rate - start_rate) * step / steps
            interval = 1.0 / rate
            num_ops = int(rate * step_duration)
            base_time = step * step_duration

            for i in range(num_ops):
                times.append(base_time + i * interval)

        return times

    @staticmethod
    def burst(
        burst_rate: int, burst_duration: float, idle_duration: float, num_bursts: int
    ) -> List[float]:
        """Burst load pattern."""
        times = []
        cycle_duration = burst_duration + idle_duration

        for burst in range(num_bursts):
            base_time = burst * cycle_duration
            interval = 1.0 / burst_rate
            num_ops = int(burst_rate * burst_duration)

            for i in range(num_ops):
                times.append(base_time + i * interval)

        return times


def format_throughput(throughput: float) -> str:
    """Format throughput for display."""
    if throughput >= 1000:
        return f"{throughput / 1000:.2f}k ops/s"
    return f"{throughput:.2f} ops/s"


def format_latency(seconds: float) -> str:
    """Format latency for display."""
    if seconds < 0.001:
        return f"{seconds * 1000000:.0f}μs"
    elif seconds < 1:
        return f"{seconds * 1000:.2f}ms"
    return f"{seconds:.3f}s"


def compare_performance(
    baseline: PerformanceStats, current: PerformanceStats
) -> Dict[str, Any]:
    """
    Compare two performance measurements.

    Returns:
        Dictionary with comparison metrics and regression indicators
    """
    mean_change = ((current.mean - baseline.mean) / baseline.mean) * 100
    p95_change = ((current.p95 - baseline.p95) / baseline.p95) * 100
    p99_change = ((current.p99 - baseline.p99) / baseline.p99) * 100

    regression = mean_change > 10  # >10% slower is regression
    improvement = mean_change < -10  # >10% faster is improvement

    return {
        "mean_change_pct": mean_change,
        "p95_change_pct": p95_change,
        "p99_change_pct": p99_change,
        "regression": regression,
        "improvement": improvement,
        "status": "regression"
        if regression
        else ("improvement" if improvement else "stable"),
    }


class ResourceMonitor:
    """Monitor system resources during tests."""

    def __init__(self):
        import psutil

        self.process = psutil.Process()
        self.samples = []

    def sample(self):
        """Take a resource usage sample."""
        sample = {
            "timestamp": time.time(),
            "cpu_percent": self.process.cpu_percent(),
            "memory_mb": self.process.memory_info().rss / 1024 / 1024,
            "threads": self.process.num_threads(),
        }
        self.samples.append(sample)
        return sample

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of resource usage."""
        if not self.samples:
            return {}

        cpu_values = [s["cpu_percent"] for s in self.samples]
        mem_values = [s["memory_mb"] for s in self.samples]

        return {
            "cpu_mean": statistics.mean(cpu_values),
            "cpu_max": max(cpu_values),
            "memory_mean": statistics.mean(mem_values),
            "memory_max": max(mem_values),
            "memory_min": min(mem_values),
            "memory_increase": max(mem_values) - min(mem_values),
            "samples_count": len(self.samples),
        }
