"""
Redis client and cache module tests.

Tests cover Redis connectivity, basic operations, caching decorator,
session management, presence caching, and rate limiting.

Uses fakeredis for unit tests (no real Redis required).
"""

import pytest

pytestmark = pytest.mark.redis
import os  # noqa: E402
import importlib  # noqa: E402
import importlib.util  # noqa: E402

# common-utils is now a native package.


import utils.config as config  # noqa: E402
import utils.logger as logger  # noqa: E402


# Check if fakeredis is available
try:
    import fakeredis

    FAKEREDIS_AVAILABLE = True
except ImportError:
    FAKEREDIS_AVAILABLE = False

# Check if real redis is available
REDIS_AVAILABLE = importlib.util.find_spec("redis") is not None


@pytest.fixture(scope="module")
def setup_module(tmp_path_factory):
    """Setup temp environment for tests."""
    temp_dir = tmp_path_factory.mktemp("redis_test")

    log_dir = str(temp_dir / "logs")
    logger.setup(log_dir=log_dir, level="DEBUG")

    yield temp_dir


@pytest.fixture
def redis_config(setup_module):
    """Setup Redis configuration for tests."""
    temp_dir = setup_module
    config_path = str(temp_dir / "config.yaml")
    db_path = str(temp_dir / "test.db")

    if os.path.exists(config_path):
        os.remove(config_path)

    default_config = {
        "database": {"type": "sqlite", "path": db_path},
        "redis": {
            "enabled": True,
            "host": "localhost",
            "port": 6379,
            "password": "",
            "db": 0,
            "ssl": False,
            "connection_pool": {"max_connections": 10, "timeout": 5},
            "key_prefix": "test:",
            "ttl": {"session": 1800, "presence": 300, "cache": 60},
        },
    }
    config.setup(config_path=config_path, default_config=default_config)

    yield

    if os.path.exists(config_path):
        os.remove(config_path)


@pytest.fixture
def redis_disabled_config(setup_module):
    """Setup config with Redis disabled."""
    temp_dir = setup_module
    config_path = str(temp_dir / "config_disabled.yaml")
    db_path = str(temp_dir / "test.db")

    if os.path.exists(config_path):
        os.remove(config_path)

    default_config = {
        "database": {"type": "sqlite", "path": db_path},
        "redis": {"enabled": False},
    }
    config.setup(config_path=config_path, default_config=default_config)

    yield

    if os.path.exists(config_path):
        os.remove(config_path)


# ==================== RedisClient Unit Tests (with fakeredis) ====================


class TestRedisClientWithFake:
    """Tests using fakeredis (no real Redis needed)."""

    def test_client_initialization(self, redis_config):
        """Test RedisClient initializes with config."""
        from src.core.database.redis_client import RedisClient

        client = RedisClient()
        assert client.enabled is True
        assert client.host == "localhost"
        assert client.port == 6379
        assert client.key_prefix == "test:"

    def test_client_disabled(self, redis_disabled_config):
        """Test RedisClient when disabled."""
        from src.core.database.redis_client import RedisClient

        client = RedisClient()
        assert client.enabled is False
        assert client.connect() is False

    def test_basic_set_get(self, redis_config):
        """Test basic SET and GET operations."""
        from src.core.database.redis_client import RedisClient

        client = RedisClient()
        # Inject fakeredis
        client._client = fakeredis.FakeRedis(decode_responses=True)
        client._connected = True

        client.set("mykey", "myvalue")
        value = client.get("mykey")
        assert value == "myvalue"

        client.close()

    def test_set_with_ttl(self, redis_config):
        """Test SET with TTL."""
        from src.core.database.redis_client import RedisClient

        client = RedisClient()
        client._client = fakeredis.FakeRedis(decode_responses=True)
        client._connected = True

        client.set("expiring", "value", ttl=10)
        assert client.get("expiring") == "value"

        remaining = client.ttl("expiring")
        assert remaining > 0 and remaining <= 10

        client.close()

    def test_delete(self, redis_config):
        """Test DELETE operation."""
        from src.core.database.redis_client import RedisClient

        client = RedisClient()
        client._client = fakeredis.FakeRedis(decode_responses=True)
        client._connected = True

        client.set("key1", "val1")
        client.set("key2", "val2")

        count = client.delete("key1", "key2")
        assert count == 2
        assert client.get("key1") is None
        assert client.get("key2") is None

        client.close()

    def test_exists(self, redis_config):
        """Test EXISTS operation."""
        from src.core.database.redis_client import RedisClient

        client = RedisClient()
        client._client = fakeredis.FakeRedis(decode_responses=True)
        client._connected = True

        assert client.exists("nonexistent") is False
        client.set("exists", "yes")
        assert client.exists("exists") is True

        client.close()

    def test_json_operations(self, redis_config):
        """Test JSON set/get operations."""
        from src.core.database.redis_client import RedisClient

        client = RedisClient()
        client._client = fakeredis.FakeRedis(decode_responses=True)
        client._connected = True

        data = {"name": "Alice", "age": 30, "tags": ["admin", "user"]}
        client.set_json("user:1", data)

        result = client.get_json("user:1")
        assert result == data
        assert result["name"] == "Alice"
        assert result["tags"] == ["admin", "user"]

        client.close()

    def test_hash_operations(self, redis_config):
        """Test hash operations."""
        from src.core.database.redis_client import RedisClient

        client = RedisClient()
        client._client = fakeredis.FakeRedis(decode_responses=True)
        client._connected = True

        client.hset("user:1", "name", "Alice")
        client.hset("user:1", "email", "alice@example.com")

        assert client.hget("user:1", "name") == "Alice"
        assert client.hget("user:1", "email") == "alice@example.com"

        all_fields = client.hgetall("user:1")
        assert all_fields["name"] == "Alice"
        assert all_fields["email"] == "alice@example.com"

        client.hdel("user:1", "email")
        assert client.hget("user:1", "email") is None

        client.close()

    def test_list_operations(self, redis_config):
        """Test list operations."""
        from src.core.database.redis_client import RedisClient

        client = RedisClient()
        client._client = fakeredis.FakeRedis(decode_responses=True)
        client._connected = True

        client.rpush("queue", "item1", "item2", "item3")
        assert client.llen("queue") == 3

        items = client.lrange("queue", 0, -1)
        assert items == ["item1", "item2", "item3"]

        assert client.lpop("queue") == "item1"
        assert client.rpop("queue") == "item3"
        assert client.llen("queue") == 1

        client.close()

    def test_set_operations(self, redis_config):
        """Test set operations."""
        from src.core.database.redis_client import RedisClient

        client = RedisClient()
        client._client = fakeredis.FakeRedis(decode_responses=True)
        client._connected = True

        client.sadd("tags", "python", "redis", "cache")
        assert client.sismember("tags", "python") is True
        assert client.sismember("tags", "java") is False

        members = client.smembers("tags")
        assert "python" in members
        assert "redis" in members

        client.srem("tags", "cache")
        assert client.sismember("tags", "cache") is False

        client.close()

    def test_counter_operations(self, redis_config):
        """Test increment/decrement operations."""
        from src.core.database.redis_client import RedisClient

        client = RedisClient()
        client._client = fakeredis.FakeRedis(decode_responses=True)
        client._connected = True

        assert client.incr("counter") == 1
        assert client.incr("counter") == 2
        assert client.incr("counter", 5) == 7
        assert client.decr("counter") == 6
        assert client.decr("counter", 3) == 3

        client.close()

    def test_key_prefix(self, redis_config):
        """Test that keys are properly prefixed."""
        from src.core.database.redis_client import RedisClient

        client = RedisClient()
        client._client = fakeredis.FakeRedis(decode_responses=True)
        client._connected = True

        client.set("mykey", "myvalue")

        # Direct access should show prefixed key
        raw_keys = client._client.keys("*")
        assert "test:mykey" in raw_keys

        client.close()

    def test_health_check(self, redis_config):
        """Test health check."""
        from src.core.database.redis_client import RedisClient

        client = RedisClient()
        client._client = fakeredis.FakeRedis(decode_responses=True)
        client._connected = True

        health = client.health_check()
        assert health["enabled"] is True
        assert health["connected"] is True
        assert health["responsive"] is True
        assert health["latency_ms"] is not None

        client.close()

    def test_context_manager(self, redis_config):
        """Test context manager usage."""
        from src.core.database.redis_client import RedisClient

        # Can't fully test connect() with fakeredis in context manager
        # but we can test the structure
        client = RedisClient()
        client._client = fakeredis.FakeRedis(decode_responses=True)
        client._connected = True

        client.set("ctx_key", "ctx_value")
        assert client.get("ctx_key") == "ctx_value"

        client.close()
        assert client._connected is False


# ==================== Cache Module Tests ====================


class TestCacheModule:
    """Tests for the cache module."""

    @pytest.fixture(autouse=True)
    def setup_cache(self, redis_config):
        """Setup cache with fakeredis backend."""
        from src.core.database import redis_client, cache

        # Create a fake client and inject it
        fake_client = redis_client.RedisClient()
        fake_client._client = fakeredis.FakeRedis(decode_responses=True)
        fake_client._connected = True
        fake_client.enabled = True

        # Set as the global client
        redis_client._default_client = fake_client

        # Reset cache stats
        cache.reset_cache_stats()

        yield fake_client

        fake_client.close()
        redis_client._default_client = None

    def test_cache_set_get(self):
        """Test basic cache set/get."""
        from src.core.database.cache import cache_set, cache_get

        assert cache_set("test_key", {"data": "value"}) is True
        result = cache_get("test_key")
        assert result == {"data": "value"}

    def test_cache_delete(self):
        """Test cache delete."""
        from src.core.database.cache import cache_set, cache_get, cache_delete

        cache_set("delete_me", "value")
        assert cache_get("delete_me") == "value"

        assert cache_delete("delete_me") is True
        assert cache_get("delete_me") is None

    def test_cache_stats(self):
        """Test cache statistics."""
        from src.core.database.cache import (
            cache_set,
            cache_get,
            cache_stats,
            reset_cache_stats,
        )

        reset_cache_stats()

        cache_set("stats_key", "value")
        cache_get("stats_key")  # Hit
        cache_get("stats_key")  # Hit
        cache_get("nonexistent")  # Miss

        stats = cache_stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 1

    def test_cached_decorator(self):
        """Test @cached decorator."""
        from src.core.database.cache import cached, reset_cache_stats

        call_count = 0

        @cached(ttl=60)
        def expensive_function(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x * 2

        reset_cache_stats()

        # First call - should execute function
        result1 = expensive_function(5)
        assert result1 == 10
        assert call_count == 1

        # Second call - should use cache
        result2 = expensive_function(5)
        assert result2 == 10
        assert call_count == 1  # Not incremented

        # Different argument - should execute function
        result3 = expensive_function(10)
        assert result3 == 20
        assert call_count == 2

    def test_cached_decorator_with_kwargs(self):
        """Test @cached decorator with keyword arguments."""
        from src.core.database.cache import cached

        call_count = 0

        @cached(ttl=60)
        def get_user(user_id: int, include_stats: bool = False) -> dict:
            nonlocal call_count
            call_count += 1
            return {"id": user_id, "stats": include_stats}

        get_user(1, include_stats=True)
        assert call_count == 1

        get_user(1, include_stats=True)
        assert call_count == 1  # Cached

        get_user(1, include_stats=False)
        assert call_count == 2  # Different kwargs

    def test_invalidate_pattern(self):
        """Test pattern-based cache invalidation."""
        from src.core.database.cache import cache_set, cache_get, invalidate_pattern

        cache_set("user:1:profile", {"name": "Alice"})
        cache_set("user:1:settings", {"theme": "dark"})
        cache_set("user:2:profile", {"name": "Bob"})

        # Invalidate all user:1:* keys
        count = invalidate_pattern("user:1:*")
        assert count == 2

        assert cache_get("user:1:profile") is None
        assert cache_get("user:1:settings") is None
        assert cache_get("user:2:profile") == {"name": "Bob"}

    def test_cache_health(self):
        """Test cache health check."""
        from src.core.database.cache import (
            cache_health,
            cache_set,
            cache_get,
            reset_cache_stats,
        )

        reset_cache_stats()
        cache_set("health_key", "value")
        cache_get("health_key")
        cache_get("miss")

        health = cache_health()
        assert health["available"] is True
        assert health["stats"]["hits"] == 1
        assert health["stats"]["misses"] == 1
        assert health["hit_rate"] == 50.0


# ==================== Session Cache Tests ====================


class TestSessionCache:
    """Tests for session caching."""

    @pytest.fixture(autouse=True)
    def setup_cache(self, redis_config):
        """Setup cache with fakeredis backend."""
        from src.core.database import redis_client

        fake_client = redis_client.RedisClient()
        fake_client._client = fakeredis.FakeRedis(decode_responses=True)
        fake_client._connected = True
        fake_client.enabled = True
        redis_client._default_client = fake_client

        yield fake_client

        fake_client.close()
        redis_client._default_client = None

    def test_cache_session(self):
        """Test session caching."""
        from src.core.database.cache import cache_session, get_cached_session

        session_data = {"ip": "127.0.0.1", "user_agent": "TestBrowser"}
        assert cache_session("sess_123", user_id=1, data=session_data) is True

        cached = get_cached_session("sess_123")
        assert cached is not None
        assert cached["user_id"] == 1
        assert cached["ip"] == "127.0.0.1"
        assert "created_at" in cached

    def test_invalidate_session(self):
        """Test session invalidation."""
        from src.core.database.cache import (
            cache_session,
            get_cached_session,
            invalidate_session,
        )

        cache_session("sess_456", user_id=2, data={})
        assert get_cached_session("sess_456") is not None

        assert invalidate_session("sess_456", user_id=2) is True
        assert get_cached_session("sess_456") is None

    def test_invalidate_user_sessions(self):
        """Test invalidating all sessions for a user."""
        from src.core.database.cache import (
            cache_session,
            get_cached_session,
            invalidate_user_sessions,
        )

        cache_session("sess_a", user_id=5, data={})
        cache_session("sess_b", user_id=5, data={})
        cache_session("sess_c", user_id=6, data={})

        count = invalidate_user_sessions(5)
        assert count == 2

        assert get_cached_session("sess_a") is None
        assert get_cached_session("sess_b") is None
        assert get_cached_session("sess_c") is not None  # Different user


# ==================== Presence Cache Tests ====================


class TestPresenceCache:
    """Tests for presence caching."""

    @pytest.fixture(autouse=True)
    def setup_cache(self, redis_config):
        """Setup cache with fakeredis backend."""
        from src.core.database import redis_client

        fake_client = redis_client.RedisClient()
        fake_client._client = fakeredis.FakeRedis(decode_responses=True)
        fake_client._connected = True
        fake_client.enabled = True
        redis_client._default_client = fake_client

        yield fake_client

        fake_client.close()
        redis_client._default_client = None

    def test_cache_presence(self):
        """Test presence caching."""
        from src.core.database.cache import cache_presence, get_cached_presence

        assert cache_presence(1, "online", "Playing games") is True

        presence = get_cached_presence(1)
        assert presence is not None
        assert presence["status"] == "online"
        assert presence["custom_status"] == "Playing games"
        assert "updated_at" in presence

    def test_get_bulk_presence(self):
        """Test bulk presence retrieval."""
        from src.core.database.cache import cache_presence, get_bulk_presence

        cache_presence(1, "online")
        cache_presence(2, "idle")
        cache_presence(3, "dnd")

        presences = get_bulk_presence([1, 2, 3, 999])
        assert len(presences) == 3
        assert presences[1]["status"] == "online"
        assert presences[2]["status"] == "idle"
        assert presences[3]["status"] == "dnd"
        assert 999 not in presences


# ==================== Rate Limiting Tests ====================


class TestRateLimiting:
    """Tests for rate limiting."""

    @pytest.fixture(autouse=True)
    def setup_cache(self, redis_config):
        """Setup cache with fakeredis backend."""
        from src.core.database import redis_client

        fake_client = redis_client.RedisClient()
        fake_client._client = fakeredis.FakeRedis(decode_responses=True)
        fake_client._connected = True
        fake_client.enabled = True
        redis_client._default_client = fake_client

        yield fake_client

        fake_client.close()
        redis_client._default_client = None

    def test_rate_limit_allowed(self):
        """Test rate limiting allows requests under limit."""
        from src.core.database.cache import check_rate_limit, reset_rate_limit

        reset_rate_limit("user:1:api")

        allowed, remaining = check_rate_limit("user:1:api", limit=5, window_seconds=60)
        assert allowed is True
        assert remaining == 4

        allowed, remaining = check_rate_limit("user:1:api", limit=5, window_seconds=60)
        assert allowed is True
        assert remaining == 3

    def test_rate_limit_exceeded(self):
        """Test rate limiting blocks requests over limit."""
        from src.core.database.cache import check_rate_limit, reset_rate_limit

        reset_rate_limit("user:2:api")

        # Use up the limit
        for i in range(5):
            allowed, _ = check_rate_limit("user:2:api", limit=5, window_seconds=60)
            assert allowed is True

        # Next request should be blocked
        allowed, remaining = check_rate_limit("user:2:api", limit=5, window_seconds=60)
        assert allowed is False
        assert remaining == 0

    def test_rate_limit_reset(self):
        """Test rate limit reset."""
        from src.core.database.cache import check_rate_limit, reset_rate_limit

        # Use up some limit
        check_rate_limit("user:3:api", limit=5, window_seconds=60)
        check_rate_limit("user:3:api", limit=5, window_seconds=60)

        # Reset
        reset_rate_limit("user:3:api")

        # Should be back to full
        allowed, remaining = check_rate_limit("user:3:api", limit=5, window_seconds=60)
        assert allowed is True
        assert remaining == 4


# ==================== Error Handling Tests ====================


class TestErrorHandling:
    """Tests for error handling."""

    def test_operation_without_connection(self, redis_config):
        """Test operations fail gracefully without connection."""
        from src.core.database.redis_client import RedisClient, RedisConnectionError

        client = RedisClient()
        # Don't connect

        with pytest.raises(RedisConnectionError):
            client.set("key", "value")

    def test_disabled_client_operations(self, redis_disabled_config):
        """Test operations fail gracefully when disabled."""
        from src.core.database.redis_client import RedisClient, RedisOperationError

        client = RedisClient()

        with pytest.raises(RedisOperationError, match="disabled"):
            client.set("key", "value")


# ==================== Integration Tests (Real Redis) ====================


class TestRealRedisIntegration:
    """Integration tests with real Redis (skipped if unavailable)."""

    def test_real_connection(self, redis_config):
        """Test connection to real Redis server."""
        from src.core.database.redis_client import RedisClient, RedisConnectionError

        client = RedisClient()
        try:
            client.connect()
            assert client.ping() is True
            client.close()
        except RedisConnectionError:
            pytest.skip("Redis server not available")
