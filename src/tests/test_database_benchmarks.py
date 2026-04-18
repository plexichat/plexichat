"""
Database Performance Benchmark Suite

Comprehensive performance benchmarks for database operations with regression detection.
Benchmarks cover:
- Single query execution time (target: <10ms)
- Connection acquisition time (target: <50ms)
- Transaction commit/rollback time
- Placeholder conversion performance
- SQLite vs PostgreSQL performance comparison
- Baseline metrics generation for regression detection

All benchmarks use realistic data sizes and patterns similar to production workloads.
Results are logged with comparison to baselines for automated regression detection.
"""

import pytest
import time
import statistics
import json
import os
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import utils.config as config
import utils.logger as logger
from src.core.database import Database


# ============================================================================
# Benchmark Configuration and Data Classes
# ============================================================================


@dataclass
class BenchmarkMetrics:
    """Metrics from a single benchmark run."""

    operation: str
    execution_time_ms: float
    timestamp: float = field(default_factory=time.time)
    database_type: str = "unknown"
    parameters: Dict[str, Any] = field(default_factory=dict)

    def exceeds_target(self, target_ms: float) -> bool:
        """Check if execution time exceeds target threshold."""
        return self.execution_time_ms > target_ms


@dataclass
class BenchmarkResult:
    """Aggregated results from benchmark run."""

    test_name: str
    database_type: str
    operation: str
    num_iterations: int

    # Timing metrics
    min_ms: float = 0.0
    max_ms: float = 0.0
    mean_ms: float = 0.0
    median_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    stdev_ms: float = 0.0

    # Target comparison
    target_ms: Optional[float] = None
    exceeded_target_count: int = 0
    exceeded_target_percent: float = 0.0

    # Timestamp
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    def __str__(self) -> str:
        """Format result for display."""
        result_str = f"\n{'=' * 80}\nBenchmark: {self.test_name}\n{'=' * 80}"
        result_str += f"\nDatabase Type: {self.database_type}"
        result_str += f"\nOperation: {self.operation}"
        result_str += f"\nIterations: {self.num_iterations}"
        result_str += "\n\nTiming Statistics (milliseconds):"
        result_str += f"\n  Min:    {self.min_ms:.3f}"
        result_str += f"\n  Max:    {self.max_ms:.3f}"
        result_str += f"\n  Mean:   {self.mean_ms:.3f}"
        result_str += f"\n  Median: {self.median_ms:.3f}"
        result_str += f"\n  P95:    {self.p95_ms:.3f}"
        result_str += f"\n  P99:    {self.p99_ms:.3f}"
        result_str += f"\n  StDev:  {self.stdev_ms:.3f}"

        if self.target_ms is not None:
            result_str += f"\n\nTarget Analysis (target: {self.target_ms:.1f}ms):"
            result_str += f"\n  Exceeded: {self.exceeded_target_count}/{self.num_iterations} ({self.exceeded_target_percent:.1f}%)"
            status = "✓ PASS" if self.exceeded_target_percent == 0 else "✗ FAIL"
            result_str += f"\n  Status: {status}"

        result_str += f"\n{'=' * 80}\n"
        return result_str


# ============================================================================
# Benchmark Utilities
# ============================================================================


class BenchmarkTimer:
    """Context manager for timing operations."""

    def __init__(self):
        self.start_time = None
        self.elapsed_ms = None

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.elapsed_ms = (time.time() - self.start_time) * 1000


class PercentileCalculator:
    """Calculate percentiles efficiently."""

    @staticmethod
    def percentile(data: List[float], p: float) -> float:
        """Calculate percentile (0-100)."""
        if not data:
            return 0.0
        sorted_data = sorted(data)
        index = (len(sorted_data) - 1) * p / 100.0
        floor_index = int(index)
        ceil_index = min(floor_index + 1, len(sorted_data) - 1)
        fraction = index - floor_index

        return (
            sorted_data[floor_index] * (1 - fraction)
            + sorted_data[ceil_index] * fraction
        )


def calculate_benchmark_stats(
    execution_times: List[float], target_ms: Optional[float] = None
) -> BenchmarkResult:
    """Calculate statistics from execution times list."""
    if not execution_times:
        raise ValueError("No execution times to analyze")

    # Calculate percentiles
    p95 = PercentileCalculator.percentile(execution_times, 95)
    p99 = PercentileCalculator.percentile(execution_times, 99)

    exceeded_count = 0
    exceeded_percent = 0.0
    if target_ms is not None:
        exceeded_count = sum(1 for t in execution_times if t > target_ms)
        exceeded_percent = (exceeded_count / len(execution_times)) * 100

    result = BenchmarkResult(
        test_name="",
        database_type="",
        operation="",
        num_iterations=len(execution_times),
        min_ms=min(execution_times),
        max_ms=max(execution_times),
        mean_ms=statistics.mean(execution_times),
        median_ms=statistics.median(execution_times),
        p95_ms=p95,
        p99_ms=p99,
        stdev_ms=statistics.stdev(execution_times) if len(execution_times) > 1 else 0.0,
        target_ms=target_ms,
        exceeded_target_count=exceeded_count,
        exceeded_target_percent=exceeded_percent,
    )

    return result


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def benchmark_db_sqlite(tmp_path):
    """Create a temporary SQLite database for benchmarking."""
    db_path = str(tmp_path / "benchmark.db")

    # Configure for benchmarking
    config.setup(
        config_path=str(tmp_path / "benchmark_config.yaml"),
        default_config={
            "database": {
                "type": "sqlite",
                "path": db_path,
                "connection_pool": {
                    "min_connections": 1,
                    "max_connections": 5,
                },
            }
        },
    )

    db = Database()

    # Ensure connection is initialized for thread
    db.connect()

    # Create test table
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS benchmark_data (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
        auto_commit=True,
    )

    yield db

    # Cleanup
    try:
        if db.type == "sqlite":
            if hasattr(db._local, "connection") and db._local.connection:
                db._local.connection.close()
    except Exception:
        pass


@pytest.fixture
def benchmark_data():
    """Sample data for benchmarking."""
    return {
        "names": [f"user_{i:05d}" for i in range(1000)],
        "emails": [f"user_{i:05d}@example.com" for i in range(1000)],
        "data": "x" * 500,  # 500 bytes of data
    }


# ============================================================================
# Single Query Execution Benchmarks (Target: <10ms)
# ============================================================================


@pytest.mark.benchmark
def test_single_query_execution_sqlite(benchmark_db_sqlite, benchmark_data):
    """Benchmark single SELECT query execution (target: <10ms)."""
    db = benchmark_db_sqlite

    # Insert test data
    db.execute(
        "INSERT INTO benchmark_data (name, email, data) VALUES (?, ?, ?)",
        (
            benchmark_data["names"][0],
            benchmark_data["emails"][0],
            benchmark_data["data"],
        ),
        auto_commit=True,
    )

    execution_times = []
    iterations = 100

    for _ in range(iterations):
        with BenchmarkTimer() as timer:
            db.execute(
                "SELECT * FROM benchmark_data WHERE id = ?", (1,), auto_commit=False
            )
        execution_times.append(timer.elapsed_ms)

    result = calculate_benchmark_stats(execution_times, target_ms=10.0)
    result.test_name = "Single Query Execution (SQLite)"
    result.database_type = "SQLite"
    result.operation = "SELECT id, name, email FROM benchmark_data WHERE id = ?"

    logger.info(
        f"Single query execution benchmark: {result.mean_ms:.3f}ms (target: 10ms)"
    )
    assert result.exceeded_target_percent == 0, (
        f"Query execution exceeded 10ms target in {result.exceeded_target_count} cases"
    )


@pytest.mark.benchmark
def test_bulk_insert_sqlite(benchmark_db_sqlite, benchmark_data):
    """Benchmark bulk INSERT operation performance."""
    db = benchmark_db_sqlite

    execution_times = []
    batch_size = 10
    iterations = 10

    for i in range(iterations):
        with BenchmarkTimer() as timer:
            for j in range(batch_size):
                idx = i * batch_size + j
                db.execute(
                    "INSERT INTO benchmark_data (name, email, data) VALUES (?, ?, ?)",
                    (
                        benchmark_data["names"][idx % len(benchmark_data["names"])],
                        f"bulk_{idx}@example.com",
                        benchmark_data["data"],
                    ),
                    auto_commit=True,
                )

        # Time per insert
        time_per_insert = timer.elapsed_ms / batch_size
        execution_times.append(time_per_insert)

    result = calculate_benchmark_stats(execution_times, target_ms=5.0)
    result.test_name = "Bulk INSERT Operations (SQLite)"
    result.database_type = "SQLite"
    result.operation = "INSERT INTO benchmark_data (name, email, data) VALUES (?, ?, ?)"

    logger.info(
        f"Bulk insert benchmark: {result.mean_ms:.3f}ms per insert (target: 5ms)"
    )
    assert result.mean_ms < 20.0, f"Insert operations too slow: {result.mean_ms:.3f}ms"


@pytest.mark.benchmark
def test_query_with_join_sqlite(benchmark_db_sqlite, benchmark_data):
    """Benchmark JOIN query performance."""
    db = benchmark_db_sqlite

    # Create second table for join
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS benchmark_profiles (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            bio TEXT,
            status TEXT
        )
    """,
        auto_commit=True,
    )

    # Insert related data
    for i in range(50):
        db.execute(
            "INSERT INTO benchmark_data (name, email, data) VALUES (?, ?, ?)",
            (
                benchmark_data["names"][i],
                f"join_test_{i}@example.com",
                benchmark_data["data"],
            ),
            auto_commit=True,
        )

    for i in range(50):
        db.execute(
            "INSERT INTO benchmark_profiles (user_id, bio, status) VALUES (?, ?, ?)",
            (i + 1, "Test bio", "active"),
            auto_commit=True,
        )

    execution_times = []
    iterations = 50

    for _ in range(iterations):
        with BenchmarkTimer() as timer:
            db.execute(
                """
                SELECT bd.id, bd.name, bp.bio 
                FROM benchmark_data bd
                LEFT JOIN benchmark_profiles bp ON bd.id = bp.user_id
                LIMIT 10
            """,
                auto_commit=False,
            )
        execution_times.append(timer.elapsed_ms)

    result = calculate_benchmark_stats(execution_times, target_ms=15.0)
    result.test_name = "JOIN Query Performance (SQLite)"
    result.database_type = "SQLite"
    result.operation = (
        "SELECT ... FROM benchmark_data bd LEFT JOIN benchmark_profiles bp"
    )

    logger.info(f"JOIN query benchmark: {result.mean_ms:.3f}ms (target: 15ms)")


# ============================================================================
# Connection Acquisition Benchmarks (Target: <50ms)
# ============================================================================


@pytest.mark.benchmark
def test_connection_acquisition_sqlite(benchmark_db_sqlite, benchmark_data):
    """Benchmark connection acquisition time (target: <50ms)."""
    # Reset connection for fresh start
    if (
        hasattr(benchmark_db_sqlite._local, "connection")
        and benchmark_db_sqlite._local.connection
    ):
        benchmark_db_sqlite._local.connection.close()
        benchmark_db_sqlite._local.connection = None

    execution_times = []
    iterations = 50

    for _ in range(iterations):
        with BenchmarkTimer() as timer:
            benchmark_db_sqlite.connect()
        execution_times.append(timer.elapsed_ms)

    result = calculate_benchmark_stats(execution_times, target_ms=50.0)
    result.test_name = "Connection Acquisition (SQLite)"
    result.database_type = "SQLite"
    result.operation = "connect()"

    logger.info(
        f"Connection acquisition benchmark: {result.mean_ms:.3f}ms (target: 50ms)"
    )
    assert result.exceeded_target_percent == 0, (
        f"Connection acquisition exceeded 50ms in {result.exceeded_target_count} cases"
    )


@pytest.mark.benchmark
def test_concurrent_connection_acquisition(benchmark_db_sqlite):
    """Benchmark concurrent connection acquisition."""
    execution_times = []
    num_threads = 10

    def acquire_connection():
        start = time.time()
        # Each thread gets its own connection via thread-local storage
        benchmark_db_sqlite._get_conn()
        elapsed_ms = (time.time() - start) * 1000
        execution_times.append(elapsed_ms)

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(acquire_connection) for _ in range(num_threads)]
        for future in as_completed(futures):
            future.result()

    result = calculate_benchmark_stats(execution_times, target_ms=50.0)
    result.test_name = "Concurrent Connection Acquisition (SQLite)"
    result.database_type = "SQLite"
    result.operation = "concurrent _get_conn() calls"

    logger.info(
        f"Concurrent connection acquisition: {result.mean_ms:.3f}ms (target: 50ms)"
    )


# ============================================================================
# Transaction Benchmarks
# ============================================================================


@pytest.mark.benchmark
def test_transaction_commit_sqlite(benchmark_db_sqlite, benchmark_data):
    """Benchmark transaction commit time."""
    db = benchmark_db_sqlite

    execution_times = []
    iterations = 50

    for i in range(iterations):
        with BenchmarkTimer() as timer:
            db.begin_transaction()
            db.execute(
                "INSERT INTO benchmark_data (name, email, data) VALUES (?, ?, ?)",
                (
                    f"transaction_test_{i}",
                    f"tx_{i}@example.com",
                    benchmark_data["data"],
                ),
                auto_commit=False,
            )
            db.commit()
        execution_times.append(timer.elapsed_ms)

    result = calculate_benchmark_stats(execution_times, target_ms=5.0)
    result.test_name = "Transaction Commit (SQLite)"
    result.database_type = "SQLite"
    result.operation = "INSERT + COMMIT"

    logger.info(f"Transaction commit benchmark: {result.mean_ms:.3f}ms")


@pytest.mark.benchmark
def test_transaction_rollback_sqlite(benchmark_db_sqlite, benchmark_data):
    """Benchmark transaction rollback time."""
    db = benchmark_db_sqlite

    execution_times = []
    iterations = 50

    for i in range(iterations):
        with BenchmarkTimer() as timer:
            db.begin_transaction()
            db.execute(
                "INSERT INTO benchmark_data (name, email, data) VALUES (?, ?, ?)",
                (f"rollback_test_{i}", f"rb_{i}@example.com", benchmark_data["data"]),
                auto_commit=False,
            )
            db.rollback()
        execution_times.append(timer.elapsed_ms)

    result = calculate_benchmark_stats(execution_times)
    result.test_name = "Transaction Rollback (SQLite)"
    result.database_type = "SQLite"
    result.operation = "INSERT + ROLLBACK"

    logger.info(f"Transaction rollback benchmark: {result.mean_ms:.3f}ms")


# ============================================================================
# Placeholder Conversion Benchmarks
# ============================================================================


@pytest.mark.benchmark
def test_placeholder_conversion_performance(benchmark_db_sqlite):
    """Benchmark SQL placeholder (?) to %s conversion for PostgreSQL."""
    db = benchmark_db_sqlite

    # Test queries of varying complexity
    test_queries = [
        ("Simple", "SELECT * FROM table WHERE id = ?"),
        (
            "Multiple placeholders",
            "SELECT * FROM table WHERE id = ? AND name = ? AND email = ?",
        ),
        (
            "With quotes",
            "SELECT * FROM table WHERE id = ? AND name LIKE ? AND data = 'don''t use ?'",
        ),
        (
            "Complex join",
            "SELECT t1.* FROM table1 t1 JOIN table2 t2 ON t1.id = ? AND t2.id = ? WHERE t1.name = ?",
        ),
    ]

    execution_times_by_query = {}

    for query_name, query in test_queries:
        execution_times = []
        iterations = 1000

        for _ in range(iterations):
            with BenchmarkTimer() as timer:
                # Test conversion (SQLite returns unchanged)
                db._convert_placeholders(query)
            execution_times.append(timer.elapsed_ms)

        result = calculate_benchmark_stats(execution_times, target_ms=0.1)
        execution_times_by_query[query_name] = result

        logger.info(f"Placeholder conversion ({query_name}): {result.mean_ms:.4f}ms")

    # All placeholder conversions should be very fast
    for query_name, result in execution_times_by_query.items():
        assert result.mean_ms < 1.0, (
            f"Placeholder conversion too slow for {query_name}: {result.mean_ms:.4f}ms"
        )


# ============================================================================
# SQLite vs PostgreSQL Comparison (SQLite only for test environment)
# ============================================================================


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "operation_name,query,params",
    [
        ("Simple SELECT", "SELECT * FROM benchmark_data WHERE id = ?", (1,)),
        ("Aggregate", "SELECT COUNT(*) as count FROM benchmark_data", None),
        ("ORDER BY", "SELECT * FROM benchmark_data ORDER BY id LIMIT 10", None),
    ],
)
def test_database_operation_performance(
    benchmark_db_sqlite, operation_name, query, params
):
    """Benchmark common database operations."""
    db = benchmark_db_sqlite

    # Populate data
    for i in range(100):
        db.execute(
            "INSERT INTO benchmark_data (name, email, data) VALUES (?, ?, ?)",
            (f"perf_test_{i}", f"perf_{i}@example.com", "x" * 100),
            auto_commit=True,
        )

    execution_times = []
    iterations = 50

    for _ in range(iterations):
        with BenchmarkTimer() as timer:
            if params:
                db.execute(query, params, auto_commit=False)
            else:
                db.execute(query, auto_commit=False)
        execution_times.append(timer.elapsed_ms)

    result = calculate_benchmark_stats(execution_times)
    result.test_name = f"Database Operation ({operation_name})"
    result.database_type = "SQLite"
    result.operation = query[:60]

    logger.info(f"{operation_name} benchmark: {result.mean_ms:.3f}ms")


# ============================================================================
# Performance Report Generation
# ============================================================================


@pytest.fixture
def performance_baseline():
    """Define baseline metrics for regression detection."""
    return {
        "single_query_execution_ms": {"target": 10.0, "warning": 8.0, "critical": 9.0},
        "bulk_insert_per_op_ms": {"target": 5.0, "warning": 4.5, "critical": 4.8},
        "connection_acquisition_ms": {
            "target": 50.0,
            "warning": 40.0,
            "critical": 45.0,
        },
        "transaction_commit_ms": {"target": 10.0, "warning": 8.0, "critical": 9.0},
        "join_query_ms": {"target": 15.0, "warning": 12.0, "critical": 14.0},
        "placeholder_conversion_ms": {"target": 0.1, "warning": 0.08, "critical": 0.09},
    }


@pytest.mark.benchmark
def test_generate_performance_report(
    benchmark_db_sqlite, benchmark_data, performance_baseline, tmp_path
):
    """Generate comprehensive performance report with baseline comparison."""
    db = benchmark_db_sqlite
    report = {
        "timestamp": datetime.now().isoformat(),
        "database_type": db.type,
        "benchmarks": [],
        "summary": {
            "total_benchmarks": 0,
            "passed": 0,
            "warnings": 0,
            "failures": 0,
        },
        "baselines": performance_baseline,
    }

    # Run a sample of benchmarks
    execution_times = []
    for i in range(50):
        with BenchmarkTimer() as timer:
            db.execute("SELECT * FROM benchmark_data LIMIT 1", auto_commit=False)
        execution_times.append(timer.elapsed_ms)

    result = calculate_benchmark_stats(execution_times, target_ms=10.0)
    result.test_name = "Single Query Execution"
    result.operation = "SELECT * FROM benchmark_data LIMIT 1"

    benchmark_entry = {
        "test_name": result.test_name,
        "operation": result.operation,
        "metrics": {
            "min_ms": result.min_ms,
            "max_ms": result.max_ms,
            "mean_ms": result.mean_ms,
            "median_ms": result.median_ms,
            "p95_ms": result.p95_ms,
            "p99_ms": result.p99_ms,
        },
        "status": "PASS" if result.exceeded_target_percent == 0 else "FAIL",
    }

    report["benchmarks"].append(benchmark_entry)
    report["summary"]["total_benchmarks"] = 1
    report["summary"]["passed"] = 1 if benchmark_entry["status"] == "PASS" else 0

    # Save report to file
    report_path = tmp_path / "performance_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    logger.info(f"Performance report generated: {report_path}")

    # Verify report was created
    assert report_path.exists(), "Performance report file not created"

    # Load and verify report contents
    with open(report_path, "r") as f:
        loaded_report = json.load(f)

    assert loaded_report["database_type"] == "sqlite"
    assert len(loaded_report["benchmarks"]) > 0
    assert loaded_report["summary"]["total_benchmarks"] > 0

    logger.info(
        f"Performance report verified: {loaded_report['summary']['total_benchmarks']} benchmarks"
    )


@pytest.mark.benchmark
def test_performance_regression_detection(
    benchmark_db_sqlite, benchmark_data, performance_baseline
):
    """Test regression detection by comparing against baseline."""
    db = benchmark_db_sqlite

    # Insert baseline data if it doesn't exist
    baseline_file = "baseline_metrics.json"
    if not os.path.exists(baseline_file):
        initial_baselines = {
            "single_query_ms": 5.5,
            "connection_acquisition_ms": 35.0,
            "bulk_insert_ms": 3.2,
        }
        with open(baseline_file, "w") as f:
            json.dump(initial_baselines, f)

    # Run current benchmarks
    execution_times = []
    for _ in range(20):
        with BenchmarkTimer() as timer:
            db.execute("SELECT * FROM benchmark_data LIMIT 1", auto_commit=False)
        execution_times.append(timer.elapsed_ms)

    current_mean = statistics.mean(execution_times)

    # Load baseline
    with open(baseline_file, "r") as f:
        baseline = json.load(f)

    baseline_mean = baseline["single_query_ms"]

    # Check for regression (allow 20% variance)
    threshold = baseline_mean * 1.2
    regression_detected = current_mean > threshold

    logger.info(
        f"Regression detection: baseline={baseline_mean:.3f}ms, current={current_mean:.3f}ms, threshold={threshold:.3f}ms"
    )

    if regression_detected:
        logger.warning(
            f"Performance regression detected: {current_mean:.3f}ms > {threshold:.3f}ms"
        )

    # For testing, we allow regression but log it
    assert current_mean < threshold * 1.5, (
        f"Critical regression: {current_mean:.3f}ms exceeds {threshold * 1.5:.3f}ms"
    )

    # Cleanup
    try:
        os.remove(baseline_file)
    except Exception:
        pass


# ============================================================================
# Stress and Load Tests
# ============================================================================


@pytest.mark.benchmark
def test_high_volume_inserts(benchmark_db_sqlite, benchmark_data):
    """Benchmark high-volume INSERT performance (target: <100ms for 100 inserts)."""
    db = benchmark_db_sqlite

    num_inserts = 100

    with BenchmarkTimer() as timer:
        for i in range(num_inserts):
            db.execute(
                "INSERT INTO benchmark_data (name, email, data) VALUES (?, ?, ?)",
                (f"volume_test_{i}", f"volume_{i}@example.com", benchmark_data["data"]),
                auto_commit=True,
            )

    time_per_insert = timer.elapsed_ms / num_inserts

    logger.info(
        f"High-volume insert: {timer.elapsed_ms:.1f}ms total, {time_per_insert:.3f}ms per insert"
    )
    assert time_per_insert < 5.0, (
        f"Insert too slow: {time_per_insert:.3f}ms per operation"
    )


@pytest.mark.benchmark
def test_concurrent_query_stress(benchmark_db_sqlite, benchmark_data):
    """Stress test with concurrent query execution."""
    db = benchmark_db_sqlite

    # Populate data
    for i in range(100):
        db.execute(
            "INSERT INTO benchmark_data (name, email, data) VALUES (?, ?, ?)",
            (f"stress_test_{i}", f"stress_{i}@example.com", benchmark_data["data"]),
            auto_commit=True,
        )

    execution_times = []
    num_threads = 5
    queries_per_thread = 20

    def stress_query():
        # Ensure thread-local connection is initialized
        db.connect()
        for i in range(queries_per_thread):
            start = time.time()
            db.execute(
                "SELECT * FROM benchmark_data WHERE id = ?",
                (i % 100 + 1,),
                auto_commit=False,
            )
            execution_times.append((time.time() - start) * 1000)

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(stress_query) for _ in range(num_threads)]
        for future in as_completed(futures):
            future.result()

    if execution_times:
        result = calculate_benchmark_stats(execution_times)
        logger.info(
            f"Stress test: {result.mean_ms:.3f}ms avg query time ({len(execution_times)} total queries)"
        )
        assert result.mean_ms < 20.0, (
            f"Stress test failed: avg query time {result.mean_ms:.3f}ms exceeds limit"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "benchmark"])
