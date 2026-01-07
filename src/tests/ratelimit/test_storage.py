"""
Tests for memory storage operations.
"""

import time
import threading
from concurrent.futures import ThreadPoolExecutor

from src.core.ratelimit.storage import MemoryStorage


class TestBasicOperations:
    """Tests for basic storage operations."""

    def test_set_and_get_bucket(self, memory_storage):
        """Test setting and getting a bucket."""
        state = {"tokens": 10, "last_update": time.monotonic()}
        memory_storage.set_bucket("test:key", state)
        retrieved = memory_storage.get_bucket("test:key")
        assert retrieved is not None
        assert retrieved["tokens"] == 10

    def test_get_nonexistent_bucket(self, memory_storage):
        """Test getting a nonexistent bucket returns None."""
        result = memory_storage.get_bucket("nonexistent:key")
        assert result is None

    def test_delete_bucket(self, memory_storage):
        """Test deleting a bucket."""
        state = {"tokens": 10}
        memory_storage.set_bucket("test:key", state)
        result = memory_storage.delete_bucket("test:key")
        assert result is True
        assert memory_storage.get_bucket("test:key") is None

    def test_delete_nonexistent_bucket(self, memory_storage):
        """Test deleting a nonexistent bucket returns False."""
        result = memory_storage.delete_bucket("nonexistent:key")
        assert result is False

    def test_clear_all(self, memory_storage):
        """Test clearing all buckets."""
        memory_storage.set_bucket("key1", {"tokens": 10})
        memory_storage.set_bucket("key2", {"tokens": 20})
        memory_storage.set_bucket("key3", {"tokens": 30})
        memory_storage.clear_all()
        assert memory_storage.get_bucket("key1") is None
        assert memory_storage.get_bucket("key2") is None
        assert memory_storage.get_bucket("key3") is None


class TestTTL:
    """Tests for TTL (time-to-live) functionality."""

    def test_bucket_expires_after_ttl(self):
        """Test bucket expires after TTL."""
        storage = MemoryStorage(cleanup_interval=0.05)
        state = {"tokens": 10}
        storage.set_bucket("test:key", state, ttl=0.1)
        assert storage.get_bucket("test:key") is not None
        time.sleep(0.15)
        assert storage.get_bucket("test:key") is None

    def test_bucket_without_ttl_persists(self, memory_storage):
        """Test bucket without TTL persists."""
        state = {"tokens": 10}
        memory_storage.set_bucket("test:key", state)
        time.sleep(0.1)
        assert memory_storage.get_bucket("test:key") is not None

    def test_ttl_can_be_updated(self):
        """Test TTL can be updated."""
        storage = MemoryStorage(cleanup_interval=0.05)
        state = {"tokens": 10}
        storage.set_bucket("test:key", state, ttl=0.1)
        storage.set_bucket("test:key", state, ttl=1.0)
        time.sleep(0.15)
        assert storage.get_bucket("test:key") is not None


class TestPrefixOperations:
    """Tests for prefix-based operations."""

    def test_get_keys_by_prefix(self, memory_storage):
        """Test getting keys by prefix."""
        memory_storage.set_bucket("user:123:route:a", {"tokens": 10})
        memory_storage.set_bucket("user:123:route:b", {"tokens": 20})
        memory_storage.set_bucket("user:456:route:a", {"tokens": 30})
        keys = memory_storage.get_keys_by_prefix("user:123")
        assert len(keys) == 2
        assert "user:123:route:a" in keys
        assert "user:123:route:b" in keys

    def test_delete_by_prefix(self, memory_storage):
        """Test deleting keys by prefix."""
        memory_storage.set_bucket("user:123:route:a", {"tokens": 10})
        memory_storage.set_bucket("user:123:route:b", {"tokens": 20})
        memory_storage.set_bucket("user:456:route:a", {"tokens": 30})
        count = memory_storage.delete_by_prefix("user:123")
        assert count == 2
        assert memory_storage.get_bucket("user:123:route:a") is None
        assert memory_storage.get_bucket("user:123:route:b") is None
        assert memory_storage.get_bucket("user:456:route:a") is not None

    def test_empty_prefix_matches_all(self, memory_storage):
        """Test empty prefix matches all keys."""
        memory_storage.set_bucket("key1", {"tokens": 10})
        memory_storage.set_bucket("key2", {"tokens": 20})
        keys = memory_storage.get_keys_by_prefix("")
        assert len(keys) == 2


class TestAtomicOperations:
    """Tests for atomic operations."""

    def test_increment(self, memory_storage):
        """Test atomic increment."""
        memory_storage.set_bucket("test:key", {"count": 0})
        result = memory_storage.increment("test:key", "count", 5)
        assert result == 5
        bucket = memory_storage.get_bucket("test:key")
        assert bucket["count"] == 5

    def test_increment_creates_field(self, memory_storage):
        """Test increment creates field if not exists."""
        memory_storage.set_bucket("test:key", {})
        result = memory_storage.increment("test:key", "count", 1)
        assert result == 1

    def test_increment_creates_bucket(self, memory_storage):
        """Test increment creates bucket if not exists."""
        result = memory_storage.increment("new:key", "count", 1)
        assert result == 1
        bucket = memory_storage.get_bucket("new:key")
        assert bucket["count"] == 1

    def test_get_and_set(self, memory_storage):
        """Test atomic get and set."""
        memory_storage.set_bucket("test:key", {"value": "old"})
        previous = memory_storage.get_and_set("test:key", "value", "new")
        assert previous == "old"
        bucket = memory_storage.get_bucket("test:key")
        assert bucket["value"] == "new"

    def test_get_and_set_with_default(self, memory_storage):
        """Test get and set with default value."""
        memory_storage.set_bucket("test:key", {})
        previous = memory_storage.get_and_set(
            "test:key", "value", "new", default="default"
        )
        assert previous == "default"


class TestListOperations:
    """Tests for list operations."""

    def test_add_to_list(self, memory_storage):
        """Test adding to a list."""
        memory_storage.set_bucket("test:key", {"timestamps": []})
        size = memory_storage.add_to_list("test:key", "timestamps", 1.0)
        assert size == 1
        size = memory_storage.add_to_list("test:key", "timestamps", 2.0)
        assert size == 2

    def test_add_to_list_max_size(self, memory_storage):
        """Test list respects max size."""
        memory_storage.set_bucket("test:key", {"timestamps": []})
        for i in range(10):
            memory_storage.add_to_list("test:key", "timestamps", float(i), max_size=5)
        bucket = memory_storage.get_bucket("test:key")
        assert len(bucket["timestamps"]) == 5
        assert bucket["timestamps"] == [5.0, 6.0, 7.0, 8.0, 9.0]

    def test_trim_list(self, memory_storage):
        """Test trimming a list."""
        memory_storage.set_bucket("test:key", {"timestamps": [1.0, 2.0, 3.0, 4.0, 5.0]})
        removed = memory_storage.trim_list("test:key", "timestamps", 3.0)
        assert removed == 2
        bucket = memory_storage.get_bucket("test:key")
        assert bucket["timestamps"] == [3.0, 4.0, 5.0]


class TestLocking:
    """Tests for locking operations."""

    def test_acquire_and_release_lock(self, memory_storage):
        """Test acquiring and releasing a lock."""
        acquired = memory_storage.acquire_lock("test:lock")
        assert acquired is True
        memory_storage.release_lock("test:lock")

    def test_lock_blocks_concurrent_access(self, memory_storage):
        """Test lock blocks concurrent access."""
        results = []

        def worker(worker_id):
            acquired = memory_storage.acquire_lock("test:lock", timeout=0.1)
            if acquired:
                results.append(worker_id)
                time.sleep(0.05)
                memory_storage.release_lock("test:lock")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) >= 1

    def test_release_unacquired_lock(self, memory_storage):
        """Test releasing an unacquired lock doesn't raise."""
        memory_storage.release_lock("nonexistent:lock")


class TestThreadSafety:
    """Tests for thread safety."""

    def test_concurrent_set_and_get(self, memory_storage):
        """Test concurrent set and get operations."""
        errors = []

        def worker(worker_id):
            try:
                for i in range(100):
                    key = f"key:{worker_id}:{i}"
                    memory_storage.set_bucket(key, {"value": i})
                    result = memory_storage.get_bucket(key)
                    if result is None or result["value"] != i:
                        errors.append(f"Mismatch for {key}")
            except Exception as e:
                errors.append(str(e))

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(worker, i) for i in range(4)]
            for f in futures:
                f.result()
        assert len(errors) == 0

    def test_concurrent_increment(self, memory_storage):
        """Test concurrent increment operations."""
        memory_storage.set_bucket("counter", {"count": 0})

        def worker():
            for _ in range(100):
                memory_storage.increment("counter", "count", 1)

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(worker) for _ in range(4)]
            for f in futures:
                f.result()
        bucket = memory_storage.get_bucket("counter")
        assert bucket["count"] == 400

    def test_concurrent_list_operations(self, memory_storage):
        """Test concurrent list operations."""
        memory_storage.set_bucket("list", {"items": []})

        def worker(worker_id):
            for i in range(50):
                memory_storage.add_to_list(
                    "list", "items", f"{worker_id}:{i}", max_size=1000
                )

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(worker, i) for i in range(4)]
            for f in futures:
                f.result()
        bucket = memory_storage.get_bucket("list")
        assert len(bucket["items"]) == 200


class TestCleanup:
    """Tests for automatic cleanup."""

    def test_cleanup_removes_expired_buckets(self):
        """Test cleanup removes expired buckets."""
        storage = MemoryStorage(cleanup_interval=0.05, max_buckets=1000)
        storage.set_bucket("key1", {"tokens": 10}, ttl=0.05)
        storage.set_bucket("key2", {"tokens": 20}, ttl=1.0)
        time.sleep(0.1)
        storage.get_bucket("trigger_cleanup")
        assert storage.get_bucket("key1") is None
        assert storage.get_bucket("key2") is not None

    def test_max_buckets_enforced(self):
        """Test max buckets limit is enforced."""
        storage = MemoryStorage(cleanup_interval=0.01, max_buckets=10)
        for i in range(20):
            storage.set_bucket(f"key:{i}", {"tokens": i, "last_update": i})
        time.sleep(0.02)
        storage.get_bucket("trigger_cleanup")
        stats = storage.get_stats()
        assert stats["bucket_count"] <= 10


class TestStats:
    """Tests for storage statistics."""

    def test_get_stats(self, memory_storage):
        """Test getting storage statistics."""
        memory_storage.set_bucket("key1", {"tokens": 10})
        memory_storage.set_bucket("key2", {"tokens": 20}, ttl=60)
        stats = memory_storage.get_stats()
        assert "bucket_count" in stats
        assert "ttl_count" in stats
        assert "max_buckets" in stats
        assert stats["bucket_count"] == 2
        assert stats["ttl_count"] == 1
