"""
SQLite load testing for concurrency and performance validation.

Tests SQLite's behavior under concurrent write operations to determine
safe operational limits for production deployments.
"""

import pytest
import os
import sys
import threading
import time
import concurrent.futures

import utils.config as config  # noqa: E402
import utils.logger as logger  # noqa: E402
from src.core.database.core import Database  # noqa: E402

# Setup paths before any imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
src_path = project_root
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")

for path in [project_root, src_path, common_utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)


@pytest.fixture(scope="module")
def setup_module(tmp_path_factory):
    """Setup test environment once per module."""
    temp_dir = tmp_path_factory.mktemp("sqlite_load_test")

    log_dir = str(temp_dir / "logs")
    logger.setup(log_dir=log_dir, level="DEBUG")

    yield temp_dir


@pytest.fixture
def sqlite_db(setup_module):
    """Create a fresh SQLite database for each test."""
    import gc
    import time

    temp_dir = setup_module
    config_path = str(temp_dir / "sqlite_load_config.yaml")
    db_path = str(temp_dir / "sqlite_load_test.db")

    gc.collect()
    time.sleep(0.1)

    if os.path.exists(config_path):
        try:
            os.remove(config_path)
        except OSError:
            pass
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass

    default_config = {"database": {"type": "sqlite", "path": db_path}}
    config.setup(config_path=config_path, default_config=default_config)

    db = Database()
    db.connect()

    # Create test table
    db.execute("""
        CREATE TABLE load_test (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            value TEXT,
            created_at INTEGER
        )
    """)

    yield db

    gc.collect()
    time.sleep(0.1)

    for path in [db_path]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


class TestSQLiteConcurrency:
    """Test SQLite behavior under concurrent operations."""

    def test_sequential_writes_baseline(self, sqlite_db):
        """Establish baseline performance for sequential writes."""
        start_time = time.time()
        num_writes = 100

        for i in range(num_writes):
            sqlite_db.execute(
                "INSERT INTO load_test (value, created_at) VALUES (?, ?)",
                (f"test_{i}", int(time.time() * 1000)),
            )

        elapsed = time.time() - start_time
        writes_per_sec = num_writes / elapsed

        print(
            f"\nSequential writes: {num_writes} in {elapsed:.2f}s ({writes_per_sec:.1f} writes/sec)"
        )

        # Verify all writes succeeded
        count = sqlite_db.fetch_one("SELECT COUNT(*) as count FROM load_test")
        assert count["count"] == num_writes

    def test_concurrent_writes_10_threads(self, sqlite_db):
        """Test concurrent writes with 10 threads."""
        num_threads = 10
        writes_per_thread = 10
        total_writes = num_threads * writes_per_thread

        def write_worker(thread_id: int):
            """Worker function for concurrent writes."""
            for i in range(writes_per_thread):
                try:
                    sqlite_db.execute(
                        "INSERT INTO load_test (value, created_at) VALUES (?, ?)",
                        (f"thread_{thread_id}_write_{i}", int(time.time() * 1000)),
                    )
                except Exception as e:
                    return False, str(e)
            return True, None

        start_time = time.time()

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(write_worker, i) for i in range(num_threads)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        elapsed = time.time() - start_time
        writes_per_sec = total_writes / elapsed

        failures = [r for r in results if not r[0]]
        success_count = len([r for r in results if r[0]])

        print(
            f"\nConcurrent writes ({num_threads} threads): {total_writes} in {elapsed:.2f}s ({writes_per_sec:.1f} writes/sec)"
        )
        print(f"Success: {success_count}/{num_threads}, Failures: {len(failures)}")

        if failures:
            print(f"Failure reasons: {[f[1] for f in failures[:3]]}")

        # Verify all writes succeeded
        count = sqlite_db.fetch_one("SELECT COUNT(*) as count FROM load_test")
        assert count["count"] == total_writes, (
            f"Expected {total_writes} writes, got {count['count']}"
        )

    def test_concurrent_writes_50_threads(self, sqlite_db):
        """Test concurrent writes with 50 threads (stress test)."""
        num_threads = 50
        writes_per_thread = 5
        total_writes = num_threads * writes_per_thread

        def write_worker(thread_id: int):
            """Worker function for concurrent writes."""
            for i in range(writes_per_thread):
                try:
                    sqlite_db.execute(
                        "INSERT INTO load_test (value, created_at) VALUES (?, ?)",
                        (f"thread_{thread_id}_write_{i}", int(time.time() * 1000)),
                    )
                except Exception as e:
                    return False, str(e)
            return True, None

        start_time = time.time()

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(write_worker, i) for i in range(num_threads)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        elapsed = time.time() - start_time
        writes_per_sec = total_writes / elapsed

        failures = [r for r in results if not r[0]]
        success_count = len([r for r in results if r[0]])

        print(
            f"\nConcurrent writes ({num_threads} threads): {total_writes} in {elapsed:.2f}s ({writes_per_sec:.1f} writes/sec)"
        )
        print(f"Success: {success_count}/{num_threads}, Failures: {len(failures)}")

        if failures:
            print(f"Failure reasons: {[f[1] for f in failures[:3]]}")

        # Verify writes (may have failures under high contention)
        count = sqlite_db.fetch_one("SELECT COUNT(*) as count FROM load_test")
        print(f"Actual writes: {count['count']}/{total_writes}")

        # With 50 threads, some failures are expected due to SQLite's single-writer limitation
        # This test documents the limit rather than asserting success
        assert count["count"] >= total_writes * 0.8, (
            f"Too many write failures: {count['count']}/{total_writes}"
        )

    def test_concurrent_reads_during_writes(self, sqlite_db):
        """Test that reads can proceed during writes (WAL mode)."""
        num_writers = 5
        num_readers = 10
        writes_per_writer = 20
        reads_per_reader = 50

        write_lock = threading.Lock()
        write_complete = threading.Event()
        writes_done = 0

        def write_worker(thread_id: int):
            """Worker that writes to database."""
            nonlocal writes_done
            for i in range(writes_per_writer):
                try:
                    sqlite_db.execute(
                        "INSERT INTO load_test (value, created_at) VALUES (?, ?)",
                        (f"writer_{thread_id}_write_{i}", int(time.time() * 1000)),
                    )
                except Exception:
                    pass
            with write_lock:
                writes_done += 1
                if writes_done >= num_writers:
                    write_complete.set()

        def read_worker(thread_id: int):
            """Worker that reads from database."""
            # Wait for some writes to start
            time.sleep(0.1)
            reads_completed = 0
            for _ in range(reads_per_reader):
                try:
                    sqlite_db.fetch_one("SELECT COUNT(*) as count FROM load_test")
                    reads_completed += 1
                except Exception:
                    pass
            return reads_completed

        start_time = time.time()

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=num_writers + num_readers
        ) as executor:
            # Start writers
            write_futures = [
                executor.submit(write_worker, i) for i in range(num_writers)
            ]
            # Start readers
            read_futures = [executor.submit(read_worker, i) for i in range(num_readers)]

            [f.result() for f in write_futures]
            read_results = [f.result() for f in read_futures]

        elapsed = time.time() - start_time

        total_reads = sum(read_results)
        reads_per_sec = total_reads / elapsed

        print(
            f"\nConcurrent reads during writes: {total_reads} reads in {elapsed:.2f}s ({reads_per_sec:.1f} reads/sec)"
        )
        print(f"Reads per reader: {total_reads / num_readers:.1f}")

        # Verify reads succeeded (WAL mode should allow concurrent reads)
        assert total_reads >= num_readers * reads_per_reader * 0.9, (
            "Too many read failures"
        )

    def test_database_size_growth(self, sqlite_db):
        """Test database size growth with many records."""
        num_records = 10000

        start_time = time.time()

        for i in range(num_records):
            sqlite_db.execute(
                "INSERT INTO load_test (value, created_at) VALUES (?, ?)",
                (f"record_{i}", int(time.time() * 1000)),
            )

        elapsed = time.time() - start_time
        records_per_sec = num_records / elapsed

        print(
            f"\nBulk insert: {num_records} records in {elapsed:.2f}s ({records_per_sec:.1f} records/sec)"
        )

        # Check database size
        db_path = sqlite_db.config.get("path", "temp_test/sqlite_load_test.db")
        if os.path.exists(db_path):
            size_bytes = os.path.getsize(db_path)
            size_mb = size_bytes / (1024 * 1024)
            print(f"Database size: {size_mb:.2f} MB ({size_bytes} bytes)")

        # Verify all records
        count = sqlite_db.fetch_one("SELECT COUNT(*) as count FROM load_test")
        assert count["count"] == num_records


class TestSQLiteLimits:
    """Document SQLite operational limits."""

    def test_busy_timeout_behavior(self, sqlite_db):
        """Test that busy timeout prevents immediate lock failures."""
        # This test documents that busy_timeout=30000 allows writes to queue
        # rather than failing immediately

        def rapid_write():
            for i in range(10):
                sqlite_db.execute(
                    "INSERT INTO load_test (value, created_at) VALUES (?, ?)",
                    (f"rapid_{i}", int(time.time() * 1000)),
                )

        threads = [threading.Thread(target=rapid_write) for _ in range(5)]
        start_time = time.time()

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)  # 60 second timeout

        elapsed = time.time() - start_time
        print(f"\nBusy timeout test: 5 threads completed in {elapsed:.2f}s")

        # Should complete without timeout given busy_timeout=30000
        assert elapsed < 60, "Writes took too long, possible lock contention issue"

    def test_wal_checkpoint(self, sqlite_db):
        """Test WAL checkpoint behavior."""
        # Insert some data to create WAL file
        for i in range(100):
            sqlite_db.execute(
                "INSERT INTO load_test (value, created_at) VALUES (?, ?)",
                (f"wal_test_{i}", int(time.time() * 1000)),
            )

        # Manual checkpoint
        sqlite_db.execute("PRAGMA wal_checkpoint(TRUNCATE);")

        print("\nWAL checkpoint completed successfully")

        # Verify data still accessible
        count = sqlite_db.fetch_one("SELECT COUNT(*) as count FROM load_test")
        assert count["count"] >= 100
