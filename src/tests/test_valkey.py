"""
Valkey client and cache module tests.
Tests use a FakeGlideClient to mock Valkey GLIDE (no real server needed).
"""

import time
import threading
from typing import Any, Dict, List, Optional, Set, Union

import pytest

# No marker needed - all tests use FakeGlideClient, no real Valkey required.
import os

import utils.config as config
import utils.logger as logger

# ---------------------------------------------------------------------------
# FakeGlideClient -- in-memory mock of the valkey-glide-sync API subset used
# by the mixins.  Mimics the exact GLIDE signatures (bytes returns, list
# args, etc.) so the abstraction layer decodes them in the same way.
# ---------------------------------------------------------------------------

_TEncodable = Union[str, bytes, bytearray, memoryview]


class FakeGlideClient:
    """In-memory fake that implements the GLIDE sync API surface used by
    ValkeyClientBase and its mixins."""

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}
        self._expiry: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._pubsub_channels: Set[str] = set()

    # -- helpers -----------------------------------------------------------

    def _raw(self, key: object) -> bytes:
        if isinstance(key, bytes):
            return key
        if isinstance(key, str):
            return key.encode("utf-8")
        return bytes(key)

    def _key(self, key: _TEncodable) -> str:
        k = key.decode("utf-8") if isinstance(key, bytes) else str(key)
        self._purge_expired()
        return k

    def _purge_expired(self) -> None:
        now = time.time()
        expired = [k for k, t in self._expiry.items() if t <= now]
        for k in expired:
            self._data.pop(k, None)
            self._expiry.pop(k, None)

    def _val(self, v: Any) -> bytes:
        if isinstance(v, bytes):
            return v
        return str(v).encode("utf-8")

    def _decode_set_arg(self, items: List[_TEncodable]) -> List[bytes]:
        return [self._raw(i) for i in items]

    # -- Core commands -----------------------------------------------------

    def set(
        self,
        key: _TEncodable,
        value: _TEncodable,
        conditional_set: Any = None,
        expiry: Any = None,
        return_old_value: bool = False,
    ) -> Optional[bytes]:
        k = self._key(key)
        old = self._data.get(k)

        from glide_sync import ConditionalChange

        if conditional_set is ConditionalChange.ONLY_IF_DOES_NOT_EXIST:
            if k in self._data:
                return None
        elif conditional_set is ConditionalChange.ONLY_IF_EXISTS:
            if k not in self._data:
                return None

        self._data[k] = self._val(value)

        if expiry is not None:
            from glide_sync import ExpirySet, ExpiryType

            if isinstance(expiry, ExpirySet):
                secs = float(expiry.value)
                if expiry.expiry_type == ExpiryType.MILLSEC:
                    secs = secs / 1000.0
                self._expiry[k] = time.time() + secs

        if return_old_value:
            return old
        return b"OK"

    def get(self, key: _TEncodable, buffer: Any = None) -> Optional[bytes]:
        k = self._key(key)
        return self._data.get(k)

    def delete(self, keys: List[_TEncodable]) -> int:
        count = 0
        for key in keys:
            k = self._key(key)
            if k in self._data:
                del self._data[k]
                self._expiry.pop(k, None)
                count += 1
        return count

    def exists(self, keys: List[_TEncodable]) -> int:
        return sum(1 for key in keys if self._key(key) in self._data)

    def expire(self, key: _TEncodable, seconds: int, option: Any = None) -> bool:
        k = self._key(key)
        if k in self._data:
            self._expiry[k] = time.time() + seconds
            return True
        return False

    def ttl(self, key: _TEncodable) -> int:
        k = self._key(key)
        if k not in self._data:
            return -2
        if k not in self._expiry:
            return -1
        remaining = int(self._expiry[k] - time.time())
        return max(0, remaining)

    def ping(self, message: Any = None) -> bytes:
        return message if message is not None else b"PONG"

    # -- Hash commands -----------------------------------------------------

    def hset(
        self,
        key: _TEncodable,
        field_value_map: Dict[_TEncodable, _TEncodable],
    ) -> int:
        k = self._key(key)
        if k not in self._data:
            self._data[k] = {}
        h = self._data[k]
        count = 0
        for f, v in field_value_map.items():
            raw_f = self._raw(f)
            raw_v = self._raw(v)
            if raw_f not in h:
                count += 1
            h[raw_f] = raw_v
        return count

    def hget(self, key: _TEncodable, field: _TEncodable) -> Optional[bytes]:
        k = self._key(key)
        if k not in self._data:
            return None
        h = self._data[k]
        return h.get(self._raw(field))

    def hgetall(self, key: _TEncodable) -> Dict[bytes, bytes]:
        k = self._key(key)
        if k not in self._data:
            return {}
        return dict(self._data[k])

    def hdel(self, key: _TEncodable, fields: List[_TEncodable]) -> int:
        k = self._key(key)
        if k not in self._data:
            return 0
        h = self._data[k]
        count = 0
        for f in fields:
            raw = self._raw(f)
            if raw in h:
                del h[raw]
                count += 1
        return count

    # -- List commands -----------------------------------------------------

    def lpush(self, key: _TEncodable, elements: List[_TEncodable]) -> int:
        k = self._key(key)
        if k not in self._data:
            self._data[k] = []
        lst: list = self._data[k]
        for e in reversed(elements):
            lst.insert(0, self._raw(e))
        return len(lst)

    def rpush(self, key: _TEncodable, elements: List[_TEncodable]) -> int:
        k = self._key(key)
        if k not in self._data:
            self._data[k] = []
        lst: list = self._data[k]
        for e in elements:
            lst.append(self._raw(e))
        return len(lst)

    def lpop(self, key: _TEncodable) -> Optional[bytes]:
        k = self._key(key)
        if k not in self._data:
            return None
        lst: list = self._data[k]
        if not lst:
            return None
        return lst.pop(0)

    def rpop(self, key: _TEncodable) -> Optional[bytes]:
        k = self._key(key)
        if k not in self._data:
            return None
        lst: list = self._data[k]
        if not lst:
            return None
        return lst.pop()

    def lrange(self, key: _TEncodable, start: int, end: int) -> List[bytes]:
        k = self._key(key)
        if k not in self._data:
            return []
        lst: list = self._data[k]
        if end == -1:
            end = len(lst) - 1
        elif end < 0:
            end = max(0, len(lst) + end)
        return lst[start : end + 1]

    def llen(self, key: _TEncodable) -> int:
        k = self._key(key)
        if k not in self._data:
            return 0
        return len(self._data[k])

    def ltrim(self, key: _TEncodable, start: int, end: int) -> str:
        k = self._key(key)
        if k not in self._data:
            return "OK"
        lst: list = self._data[k]
        if end == -1:
            end = len(lst) - 1
        elif end < 0:
            end = max(0, len(lst) + end)
        self._data[k] = lst[start : end + 1]
        return "OK"

    # -- Set commands ------------------------------------------------------

    def sadd(self, key: _TEncodable, members: List[_TEncodable]) -> int:
        k = self._key(key)
        if k not in self._data:
            self._data[k] = set()
        s: set = self._data[k]
        before = len(s)
        s.update(self._decode_set_arg(members))
        return len(s) - before

    def srem(self, key: _TEncodable, members: List[_TEncodable]) -> int:
        k = self._key(key)
        if k not in self._data:
            return 0
        s: set = self._data[k]
        before = len(s)
        for m in self._decode_set_arg(members):
            s.discard(m)
        return before - len(s)

    def smembers(self, key: _TEncodable) -> Set[bytes]:
        k = self._key(key)
        if k not in self._data:
            return set()
        return set(self._data[k])

    def sismember(self, key: _TEncodable, member: _TEncodable) -> bool:
        k = self._key(key)
        if k not in self._data:
            return False
        s: set = self._data[k]
        return self._raw(member) in s

    # -- Counter commands --------------------------------------------------

    def incr(self, key: _TEncodable) -> int:
        k = self._key(key)
        val = int(self._data.get(k, 0)) + 1
        self._data[k] = str(val).encode("utf-8")
        return val

    def decr(self, key: _TEncodable) -> int:
        k = self._key(key)
        val = int(self._data.get(k, 0)) - 1
        self._data[k] = str(val).encode("utf-8")
        return val

    def incrby(self, key: _TEncodable, amount: int) -> int:
        k = self._key(key)
        val = int(self._data.get(k, 0)) + amount
        self._data[k] = str(val).encode("utf-8")
        return val

    def decrby(self, key: _TEncodable, amount: int) -> int:
        k = self._key(key)
        val = int(self._data.get(k, 0)) - amount
        self._data[k] = str(val).encode("utf-8")
        return val

    # -- Pub/Sub commands --------------------------------------------------

    def publish(self, message: _TEncodable, channel: _TEncodable) -> int:
        return 0

    def subscribe(self, channels: Set[str], timeout_ms: int = 0) -> None:
        self._pubsub_channels.update(channels)

    def unsubscribe(
        self, channels: Optional[Set[str]] = None, timeout_ms: int = 0
    ) -> None:
        if channels is None:
            self._pubsub_channels.clear()
        else:
            self._pubsub_channels -= channels

    def get_pubsub_message(self) -> Any:
        raise NotImplementedError("No messages in fake")

    def try_get_pubsub_message(self) -> Optional[Any]:
        return None

    # -- Misc commands -----------------------------------------------------

    def keys(self, pattern: _TEncodable = "*") -> List[bytes]:
        self._purge_expired()
        pat = pattern.decode("utf-8") if isinstance(pattern, bytes) else str(pattern)
        import fnmatch

        return [
            k.encode("utf-8")
            for k in sorted(self._data.keys())
            if fnmatch.fnmatch(k, pat)
        ]

    # -- Scan commands -----------------------------------------------------

    def scan(
        self,
        cursor: _TEncodable,
        match: Optional[_TEncodable] = None,
        count: Optional[int] = None,
    ) -> List[Union[bytes, List[bytes]]]:
        self._purge_expired()
        all_keys = sorted(self._data.keys())
        if match:
            import fnmatch

            pat = match.decode("utf-8") if isinstance(match, bytes) else str(match)
            all_keys = [k for k in all_keys if fnmatch.fnmatch(k, pat)]
        return [b"0", [k.encode("utf-8") for k in all_keys]]

    # -- Script support ----------------------------------------------------

    def invoke_script(
        self,
        script: Any,
        keys: Optional[List[_TEncodable]] = None,
        args: Optional[List[_TEncodable]] = None,
    ) -> Any:
        code = str(script)
        ks = [self._key(k) for k in (keys or [])]
        av = [
            a.decode("utf-8") if isinstance(a, bytes) else str(a) for a in (args or [])
        ]

        if "get KEYS[1]" in code and "del KEYS[1]" in code:
            k = ks[0] if ks else None
            if k and k in self._data:
                val = self._data[k]
                if av and self._val(av[0]) == val:
                    del self._data[k]
                    self._expiry.pop(k, None)
                    return 1
            return 0
        return []

    def close(self) -> None:
        self._data.clear()
        self._expiry.clear()
        self._pubsub_channels.clear()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def setup_module(tmp_path_factory):
    temp_dir = tmp_path_factory.mktemp("redis_test")
    log_dir = str(temp_dir / "logs")
    logger.setup(log_dir=log_dir, level="DEBUG")
    yield temp_dir


@pytest.fixture
def redis_config(setup_module):
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


def _inject_fake_client(
    client_obj: Any, fake: Optional[FakeGlideClient] = None
) -> FakeGlideClient:
    f = fake or FakeGlideClient()
    client_obj._client = f
    client_obj._connected = True
    client_obj.enabled = True
    return f


# ==================== ValkeyClient Unit Tests ====================


class TestValkeyClientWithFake:
    def test_client_initialization(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        assert client.enabled is True
        assert client.host == "localhost"
        assert client.port == 6379
        assert client.key_prefix == "test:"

    def test_client_disabled(self, redis_disabled_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        assert client.enabled is False
        assert client.connect() is False

    def test_basic_set_get(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_client(client)

        client.set("mykey", "myvalue")
        value = client.get("mykey")
        assert value == "myvalue"

        client.close()

    def test_set_with_ttl(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_client(client)

        client.set("expiring", "value", ttl=10)
        assert client.get("expiring") == "value"

        remaining = client.ttl("expiring")
        assert remaining > 0 and remaining <= 10

        client.close()

    def test_delete(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_client(client)

        client.set("key1", "val1")
        client.set("key2", "val2")

        count = client.delete("key1", "key2")
        assert count == 2
        assert client.get("key1") is None
        assert client.get("key2") is None

        client.close()

    def test_exists(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_client(client)

        assert client.exists("nonexistent") is False
        client.set("exists", "yes")
        assert client.exists("exists") is True

        client.close()

    def test_json_operations(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_client(client)

        data = {"name": "Alice", "age": 30, "tags": ["admin", "user"]}
        client.set_json("user:1", data)

        result = client.get_json("user:1")
        assert result == data
        assert result["name"] == "Alice"
        assert result["tags"] == ["admin", "user"]

        client.close()

    def test_hash_operations(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_client(client)

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
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_client(client)

        client.rpush("queue", "item1", "item2", "item3")
        assert client.llen("queue") == 3

        items = client.lrange("queue", 0, -1)
        assert items == ["item1", "item2", "item3"]

        assert client.lpop("queue") == "item1"
        assert client.rpop("queue") == "item3"
        assert client.llen("queue") == 1

        client.close()

    def test_set_operations(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_client(client)

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
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_client(client)

        assert client.incr("counter") == 1
        assert client.incr("counter") == 2
        assert client.incr("counter", 5) == 7
        assert client.decr("counter") == 6
        assert client.decr("counter", 3) == 3

        client.close()

    def test_key_prefix(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        fake = _inject_fake_client(client)

        client.set("mykey", "myvalue")

        raw_keys = fake.keys("*")
        assert b"test:mykey" in raw_keys

        client.close()

    def test_health_check(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_client(client)

        health = client.health_check()
        assert health["enabled"] is True
        assert health["connected"] is True
        assert health["responsive"] is True
        assert health["latency_ms"] is not None

        client.close()

    def test_context_manager(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_client(client)

        client.set("ctx_key", "ctx_value")
        assert client.get("ctx_key") == "ctx_value"

        client.close()
        assert client._connected is False


# ==================== Cache Module Tests ====================


class TestCacheModule:
    @pytest.fixture(autouse=True)
    def setup_cache(self, redis_config):
        from src.core.database import valkey_client, cache

        fake_client = valkey_client.ValkeyClient()
        _inject_fake_client(fake_client)
        valkey_client._default_client = fake_client

        cache.reset_cache_stats()

        yield fake_client

        fake_client.close()
        valkey_client._default_client = None

    def test_cache_set_get(self):
        from src.core.database.cache import cache_set, cache_get

        assert cache_set("test_key", {"data": "value"}) is True
        result = cache_get("test_key")
        assert result == {"data": "value"}

    def test_cache_delete(self):
        from src.core.database.cache import cache_set, cache_get, cache_delete

        cache_set("delete_me", "value")
        assert cache_get("delete_me") == "value"

        assert cache_delete("delete_me") is True
        assert cache_get("delete_me") is None

    def test_cache_stats(self):
        from src.core.database.cache import (
            cache_set,
            cache_get,
            cache_stats,
            reset_cache_stats,
        )

        reset_cache_stats()

        cache_set("stats_key", "value")
        cache_get("stats_key")
        cache_get("stats_key")
        cache_get("nonexistent")

        stats = cache_stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 1

    def test_cached_decorator(self):
        from src.core.database.cache import cached, reset_cache_stats

        call_count = 0

        @cached(ttl=60)
        def expensive_function(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x * 2

        reset_cache_stats()

        result1 = expensive_function(5)
        assert result1 == 10
        assert call_count == 1

        result2 = expensive_function(5)
        assert result2 == 10
        assert call_count == 1

        result3 = expensive_function(10)
        assert result3 == 20
        assert call_count == 2

    def test_cached_decorator_with_kwargs(self):
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
        assert call_count == 1

        get_user(1, include_stats=False)
        assert call_count == 2

    def test_invalidate_pattern(self):
        from src.core.database.cache import cache_set, cache_get, invalidate_pattern

        cache_set("user:1:profile", {"name": "Alice"})
        cache_set("user:1:settings", {"theme": "dark"})
        cache_set("user:2:profile", {"name": "Bob"})

        count = invalidate_pattern("user:1:*")
        assert count >= 2

        assert cache_get("user:1:profile") is None
        assert cache_get("user:1:settings") is None
        assert cache_get("user:2:profile") == {"name": "Bob"}

    def test_cache_health(self):
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
    @pytest.fixture(autouse=True)
    def setup_cache(self, redis_config):
        from src.core.database import valkey_client

        fake_client = valkey_client.ValkeyClient()
        _inject_fake_client(fake_client)
        valkey_client._default_client = fake_client

        yield fake_client

        fake_client.close()
        valkey_client._default_client = None

    def test_cache_session(self):
        from src.core.database.cache import cache_session, get_cached_session

        session_data = {"ip": "127.0.0.1", "user_agent": "TestBrowser"}
        assert cache_session("sess_123", user_id=1, data=session_data) is True

        cached = get_cached_session("sess_123")
        assert cached is not None
        assert cached["user_id"] == 1
        assert cached["ip"] == "127.0.0.1"
        assert "created_at" in cached

    def test_invalidate_session(self):
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
        from src.core.database.cache import (
            cache_session,
            get_cached_session,
            invalidate_user_sessions,
        )

        cache_session("sess_a", user_id=5, data={})
        cache_session("sess_b", user_id=5, data={})
        cache_session("sess_c", user_id=6, data={})

        count = invalidate_user_sessions(5)
        assert count >= 2

        assert get_cached_session("sess_a") is None
        assert get_cached_session("sess_b") is None
        assert get_cached_session("sess_c") is not None


# ==================== Presence Cache Tests ====================


class TestPresenceCache:
    @pytest.fixture(autouse=True)
    def setup_cache(self, redis_config):
        from src.core.database import valkey_client

        fake_client = valkey_client.ValkeyClient()
        _inject_fake_client(fake_client)
        valkey_client._default_client = fake_client

        yield fake_client

        fake_client.close()
        valkey_client._default_client = None

    def test_cache_presence(self):
        from src.core.database.cache import cache_presence, get_cached_presence

        assert cache_presence(1, "online", "Playing games") is True

        presence = get_cached_presence(1)
        assert presence is not None
        assert presence["status"] == "online"
        assert presence["custom_status"] == "Playing games"
        assert "updated_at" in presence

    def test_get_bulk_presence(self):
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
    @pytest.fixture(autouse=True)
    def setup_cache(self, redis_config):
        from src.core.database import valkey_client

        fake_client = valkey_client.ValkeyClient()
        _inject_fake_client(fake_client)
        valkey_client._default_client = fake_client

        yield fake_client

        fake_client.close()
        valkey_client._default_client = None

    def test_rate_limit_allowed(self):
        from src.core.database.cache import check_rate_limit, reset_rate_limit

        reset_rate_limit("user:1:api")

        allowed, remaining = check_rate_limit("user:1:api", limit=5, window_seconds=60)
        assert allowed is True
        assert remaining == 4

        allowed, remaining = check_rate_limit("user:1:api", limit=5, window_seconds=60)
        assert allowed is True
        assert remaining == 3

    def test_rate_limit_exceeded(self):
        from src.core.database.cache import check_rate_limit, reset_rate_limit

        reset_rate_limit("user:2:api")

        for _ in range(5):
            allowed, _ = check_rate_limit("user:2:api", limit=5, window_seconds=60)
            assert allowed is True

        allowed, remaining = check_rate_limit("user:2:api", limit=5, window_seconds=60)
        assert allowed is False
        assert remaining == 0

    def test_rate_limit_reset(self):
        from src.core.database.cache import check_rate_limit, reset_rate_limit

        check_rate_limit("user:3:api", limit=5, window_seconds=60)
        check_rate_limit("user:3:api", limit=5, window_seconds=60)

        reset_rate_limit("user:3:api")

        allowed, remaining = check_rate_limit("user:3:api", limit=5, window_seconds=60)
        assert allowed is True
        assert remaining == 4


# ==================== Error Handling Tests ====================


class TestErrorHandling:
    def test_operation_without_connection(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient, ValkeyConnectionError

        client = ValkeyClient()

        with pytest.raises(ValkeyConnectionError):
            client.set("key", "value")

    def test_disabled_client_operations(self, redis_disabled_config):
        from src.core.database.valkey_client import ValkeyClient, ValkeyOperationError

        client = ValkeyClient()

        with pytest.raises(ValkeyOperationError, match="disabled"):
            client.set("key", "value")


# ==================== Integration Tests (Real Valkey) ====================


class TestRealRedisIntegration:
    def test_real_connection(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient, ValkeyConnectionError

        client = ValkeyClient()
        try:
            client.connect()
            assert client.ping() is True
            client.close()
        except ValkeyConnectionError:
            pytest.skip("Redis/Valkey server not available")
