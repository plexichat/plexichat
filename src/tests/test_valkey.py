"""
Valkey client and cache module tests.
Tests use a FakeGlideClient to mock Valkey GLIDE (no real server needed).
"""

import asyncio
import time
import threading
import hashlib
from collections import deque
from typing import Any, Dict, List, Optional, Set, Union

import pytest

# No marker needed - all tests use FakeGlideClient, no real Valkey required.
import os

import utils.config as config
from src.tests._conftest.logger_isolation import isolated_logger

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

        import importlib

        ConditionalChange = importlib.import_module("glide_sync").ConditionalChange

        if conditional_set is ConditionalChange.ONLY_IF_DOES_NOT_EXIST:
            if k in self._data:
                return None
        elif conditional_set is ConditionalChange.ONLY_IF_EXISTS:
            if k not in self._data:
                return None

        self._data[k] = self._val(value)

        if expiry is not None:
            import importlib

            glide_sync = importlib.import_module("glide_sync")
            ExpirySet = glide_sync.ExpirySet
            ExpiryType = glide_sync.ExpiryType

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

    def mget(self, keys: List[_TEncodable]) -> List[Optional[bytes]]:
        return [self._data.get(self._key(k)) for k in keys]

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

    # GLIDE identifies scripts by SHA1 of their source; str(Script) is only
    # a repr, so map the known client-side scripts back via their hash.
    _LOCK_SCRIPT_SHA1 = hashlib.sha1(
        b"""
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """,
    ).hexdigest()
    _INCR_EXPIRE_SCRIPT_SHA1 = hashlib.sha1(
        b"""
local current = redis.call("INCR", KEYS[1])
if current == 1 then
    redis.call("EXPIRE", KEYS[1], ARGV[1])
end
return current
""",
    ).hexdigest()

    def invoke_script(
        self,
        script: Any,
        keys: Optional[List[_TEncodable]] = None,
        args: Optional[List[_TEncodable]] = None,
    ) -> Any:
        code = str(script)
        script_hash = script.get_hash() if hasattr(script, "get_hash") else None
        ks = [self._key(k) for k in (keys or [])]
        av = [
            a.decode("utf-8") if isinstance(a, bytes) else str(a) for a in (args or [])
        ]

        if script_hash == self._INCR_EXPIRE_SCRIPT_SHA1 or (
            "INCR" in code and "EXPIRE" in code
        ):
            # Atomic rate-limit script: INCR KEYS[1]; EXPIRE on first hit.
            k = ks[0] if ks else None
            if k is None:
                return 0
            window = int(av[0]) if av else 60
            current = int(self._data.get(k, b"0") or b"0") + 1
            self._data[k] = str(current)
            if current == 1:
                self._expiry[k] = time.time() + window
            return current

        if script_hash == self._LOCK_SCRIPT_SHA1 or (
            "get KEYS[1]" in code and "del KEYS[1]" in code
        ):
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
    # ``isolated_logger`` restores the level after the module so this DEBUG
    # setting cannot leak into later tests in the same worker.
    with isolated_logger(log_dir=log_dir, level="DEBUG"):
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
        from src.tests.fixtures.valkey_docker import _tcp_reachable

        # Cheap reachability pre-check: with no server listening GLIDE takes
        # its full request timeout to report the refusal, and the test then
        # skips anyway. Refusing to start the connection is the same outcome,
        # minus the ~2s wait.
        redis_settings = config.get("redis") or {}
        if not _tcp_reachable(
            redis_settings.get("host", "localhost"),
            int(redis_settings.get("port", 6379)),
        ):
            pytest.skip("Redis/Valkey server not available")

        client = ValkeyClient()
        try:
            client.connect()
            assert client.ping() is True
            client.close()
        except ValkeyConnectionError:
            pytest.skip("Redis/Valkey server not available")


# ==================== Async Client Tests ====================


class FakeAsyncGlideClient(FakeGlideClient):
    """Awaitable mirror of :class:`FakeGlideClient` for the ``a*`` methods.

    GLIDE's two packages share their option and error types (both are
    re-exported from ``glide_shared``), so the async surface can delegate to
    the sync fake and exercise the real encode/decode paths instead of a
    paraphrase of them.
    """

    def __init__(self, mirror: Optional[FakeGlideClient] = None) -> None:
        """``mirror`` aliases another fake's store.

        That models a second connection to the same server: distinct
        handles over one keyspace, so a write on either path is visible to
        the other. (Aliasing the *instance* would not work -- the async
        overrides would shadow the sync methods for both callers.)
        """
        super().__init__()
        if mirror is not None:
            self._data = mirror._data
            self._expiry = mirror._expiry
            self._pubsub_channels = mirror._pubsub_channels
        self._inbox: "deque[Any]" = deque()

    def feed_pubsub_message(self, message: Any) -> None:
        self._inbox.append(message)

    def try_get_pubsub_message(self) -> Optional[Any]:
        return self._inbox.popleft() if self._inbox else None

    async def set(self, *args: Any, **kwargs: Any) -> Any:
        return super().set(*args, **kwargs)

    async def get(self, *args: Any, **kwargs: Any) -> Any:
        return super().get(*args, **kwargs)

    async def mget(self, *args: Any, **kwargs: Any) -> Any:
        return super().mget(*args, **kwargs)

    async def delete(self, *args: Any, **kwargs: Any) -> Any:
        return super().delete(*args, **kwargs)

    async def exists(self, *args: Any, **kwargs: Any) -> Any:
        return super().exists(*args, **kwargs)

    async def expire(self, *args: Any, **kwargs: Any) -> Any:
        return super().expire(*args, **kwargs)

    async def ttl(self, *args: Any, **kwargs: Any) -> Any:
        return super().ttl(*args, **kwargs)

    async def ping(self, *args: Any, **kwargs: Any) -> Any:
        return super().ping(*args, **kwargs)

    async def hset(self, *args: Any, **kwargs: Any) -> Any:
        return super().hset(*args, **kwargs)

    async def hget(self, *args: Any, **kwargs: Any) -> Any:
        return super().hget(*args, **kwargs)

    async def hgetall(self, *args: Any, **kwargs: Any) -> Any:
        return super().hgetall(*args, **kwargs)

    async def hdel(self, *args: Any, **kwargs: Any) -> Any:
        return super().hdel(*args, **kwargs)

    async def lpush(self, *args: Any, **kwargs: Any) -> Any:
        return super().lpush(*args, **kwargs)

    async def rpush(self, *args: Any, **kwargs: Any) -> Any:
        return super().rpush(*args, **kwargs)

    async def lpop(self, *args: Any, **kwargs: Any) -> Any:
        return super().lpop(*args, **kwargs)

    async def rpop(self, *args: Any, **kwargs: Any) -> Any:
        return super().rpop(*args, **kwargs)

    async def lrange(self, *args: Any, **kwargs: Any) -> Any:
        return super().lrange(*args, **kwargs)

    async def llen(self, *args: Any, **kwargs: Any) -> Any:
        return super().llen(*args, **kwargs)

    async def ltrim(self, *args: Any, **kwargs: Any) -> Any:
        return super().ltrim(*args, **kwargs)

    async def sadd(self, *args: Any, **kwargs: Any) -> Any:
        return super().sadd(*args, **kwargs)

    async def srem(self, *args: Any, **kwargs: Any) -> Any:
        return super().srem(*args, **kwargs)

    async def smembers(self, *args: Any, **kwargs: Any) -> Any:
        return super().smembers(*args, **kwargs)

    async def sismember(self, *args: Any, **kwargs: Any) -> Any:
        return super().sismember(*args, **kwargs)

    async def incr(self, *args: Any, **kwargs: Any) -> Any:
        return super().incr(*args, **kwargs)

    async def incrby(self, *args: Any, **kwargs: Any) -> Any:
        return super().incrby(*args, **kwargs)

    async def decr(self, *args: Any, **kwargs: Any) -> Any:
        return super().decr(*args, **kwargs)

    async def decrby(self, *args: Any, **kwargs: Any) -> Any:
        return super().decrby(*args, **kwargs)

    async def publish(self, *args: Any, **kwargs: Any) -> Any:
        return super().publish(*args, **kwargs)

    async def subscribe(self, *args: Any, **kwargs: Any) -> Any:
        return super().subscribe(*args, **kwargs)

    async def unsubscribe(self, *args: Any, **kwargs: Any) -> Any:
        return super().unsubscribe(*args, **kwargs)

    async def scan(self, *args: Any, **kwargs: Any) -> Any:
        return super().scan(*args, **kwargs)

    async def invoke_script(self, *args: Any, **kwargs: Any) -> Any:
        return super().invoke_script(*args, **kwargs)

    async def close(self, *args: Any, **kwargs: Any) -> Any:
        return super().close(*args, **kwargs)


def _inject_fake_async_client(
    client_obj: Any, fake: Optional[FakeAsyncGlideClient] = None
) -> FakeAsyncGlideClient:
    """Attach an async fake **without** opening the sync handle.

    Leaving ``_client`` unset is the point: it proves the async methods do
    not quietly fall back to the blocking handle.
    """
    f = fake or FakeAsyncGlideClient()
    client_obj._aclient = f
    client_obj._aconnected = True
    client_obj.enabled = True
    return f


def _inject_shared_fake_client(client_obj: Any) -> FakeAsyncGlideClient:
    """Open both handles over one keyspace (see ``FakeAsyncGlideClient``)."""
    sync_fake = FakeGlideClient()
    async_fake = FakeAsyncGlideClient(mirror=sync_fake)
    client_obj._client = sync_fake
    client_obj._aclient = async_fake
    client_obj._connected = True
    client_obj._aconnected = True
    client_obj.enabled = True
    return async_fake


class TestValkeyClientAsyncWithFake:
    """The ``a*`` surface: same keyspace, awaited instead of blocking."""

    @pytest.mark.asyncio
    async def test_async_set_get_roundtrip(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_async_client(client)

        assert await client.aset("akey", "avalue") is True
        assert await client.aget("akey") == "avalue"

    @pytest.mark.asyncio
    async def test_async_and_sync_share_one_keyspace(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        fake = _inject_shared_fake_client(client)

        # Two handles, one keyspace: a value written on the async path must
        # be visible to the blocking path, and vice versa.
        await client.aset("shared", "written-async")
        assert client.get("shared") == "written-async"
        # The stored key carries the client prefix on both paths.
        assert "test:shared" in fake._data

        client.set("written-sync", "v")
        assert await client.aget("written-sync") == "v"
        assert "test:written-sync" in fake._data

    @pytest.mark.asyncio
    async def test_async_expiry_and_ttl(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_async_client(client)

        await client.aset("aexp", "v", ttl=30)
        remaining = await client.attl("aexp")
        assert 0 < remaining <= 30
        assert await client.aexpire("aexp", 60) is True

    @pytest.mark.asyncio
    async def test_async_delete_and_exists(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_async_client(client)

        await client.aset("k1", "v1")
        await client.aset("k2", "v2")
        assert await client.aexists("k1") is True
        assert await client.adelete("k1", "k2") == 2
        assert await client.aexists("k1") is False

    @pytest.mark.asyncio
    async def test_async_json_roundtrip_and_mget(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_async_client(client)

        data = {"name": "Alice", "tags": ["a", "b"], "n": 7}
        assert await client.aset_json("ajson", data) is True
        assert await client.aget_json("ajson") == data

        await client.aset_json("ajson2", {"name": "Bob"})
        bulk = await client.amget_json(["ajson", "ajson2", "missing"])
        assert set(bulk) == {"ajson", "ajson2"}
        assert bulk["ajson2"] == {"name": "Bob"}

    @pytest.mark.asyncio
    async def test_async_mget_json_empty_keys_is_noop(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_async_client(client)
        assert await client.amget_json([]) == {}

    @pytest.mark.asyncio
    async def test_async_hash_operations(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_async_client(client)

        await client.ahset("ahash", "f1", "v1")
        await client.ahmset("ahash", {"f2": "v2", "f3": "v3"})
        assert await client.ahget("ahash", "f1") == "v1"
        assert (await client.ahgetall("ahash"))["f3"] == "v3"
        assert await client.ahdel("ahash", "f1") == 1
        assert await client.ahget("ahash", "f1") is None

    @pytest.mark.asyncio
    async def test_async_list_operations(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_async_client(client)

        await client.arpush("aqueue", "i1", "i2", "i3")
        assert await client.allen("aqueue") == 3
        assert await client.alrange("aqueue", 0, -1) == ["i1", "i2", "i3"]
        assert await client.alpop("aqueue") == "i1"
        assert await client.arpop("aqueue") == "i3"
        await client.altrim("aqueue", 0, 0)
        assert await client.allen("aqueue") == 1
        await client.alpush("aqueue", "i0")
        assert await client.alpop("aqueue") == "i0"

    @pytest.mark.asyncio
    async def test_async_set_operations(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_async_client(client)

        await client.asadd("atags", "x", "y")
        assert await client.asismember("atags", "x") is True
        assert await client.asismember("atags", "z") is False
        assert "y" in await client.asmembers("atags")
        assert await client.asrem("atags", "y") == 1

    @pytest.mark.asyncio
    async def test_async_counters(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_async_client(client)

        assert await client.aincr("actr") == 1
        assert await client.aincr("actr", 5) == 6
        assert await client.adecr("actr", 2) == 4

    @pytest.mark.asyncio
    async def test_async_scan_keys_and_flush(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_async_client(client)

        await client.aset("scan:1", "a")
        await client.aset("scan:2", "b")
        await client.aset("other", "c")

        keys = await client.akeys("scan:*")
        assert sorted(keys) == ["scan:1", "scan:2"]

        assert await client.aflush_prefix() == 3
        assert await client.akeys("*") == []

    @pytest.mark.asyncio
    async def test_async_ping_and_health_check(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_async_client(client)

        assert await client.aping() is True
        health = await client.ahealth_check()
        assert health["connected"] is True
        assert health["responsive"] is True
        assert health["latency_ms"] is not None

    @pytest.mark.asyncio
    async def test_async_lock_acquire_release(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_async_client(client)

        token = await client.aacquire_lock("alock", timeout=0.5)
        assert token is not None
        # The lock is exclusive: a second acquisition spins out and gives up.
        assert await client.aacquire_lock("alock", timeout=0.2) is None
        assert await client.arelease_lock("alock", token) is True
        assert await client.aacquire_lock("alock", timeout=0.5) is not None

    @pytest.mark.asyncio
    async def test_async_lock_does_not_sleep_the_old_way(self, redis_config):
        """A contended lock must yield, not spin on a blocking sleep."""
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_async_client(client)
        await client.aacquire_lock("busylock", timeout=0.5)

        progressed = False

        async def _other_work() -> None:
            nonlocal progressed
            progressed = True

        task = asyncio.ensure_future(_other_work())
        # Blocks for its whole timeout only if the wait is a blocking sleep.
        assert await client.aacquire_lock("busylock", timeout=0.3) is None
        await task
        assert progressed is True

    @pytest.mark.asyncio
    async def test_async_pubsub_subscribe_and_receive(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        fake = _inject_fake_async_client(client)

        await client.asubscribe("achan")
        assert "test:achan" in fake._pubsub_channels

        assert await client.atry_get_pubsub_message() is None
        fake.feed_pubsub_message("payload")
        assert await client.atry_get_pubsub_message() == "payload"

        await client.aunsubscribe("achan")
        assert "test:achan" not in fake._pubsub_channels

    @pytest.mark.asyncio
    async def test_async_publish_returns_subscriber_count(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_async_client(client)
        assert await client.apublish("achan", "hello") == 0

    @pytest.mark.asyncio
    async def test_async_requires_its_own_connection(self, redis_config):
        """The async surface must refuse to run on the sync handle alone."""
        from src.core.database.valkey_client import (
            ValkeyClient,
            ValkeyConnectionError,
        )

        client = ValkeyClient()
        _inject_fake_client(client)  # sync handle only

        assert client.get("x") is None  # sync still works
        with pytest.raises(ValkeyConnectionError):
            await client.aget("x")

    @pytest.mark.asyncio
    async def test_async_disabled_client_raises_operation_error(
        self, redis_disabled_config
    ):
        from src.core.database.valkey_client import ValkeyClient, ValkeyOperationError

        client = ValkeyClient()
        client._aclient = FakeAsyncGlideClient()
        client._aconnected = True

        with pytest.raises(ValkeyOperationError, match="disabled"):
            await client.aget("x")

    @pytest.mark.asyncio
    async def test_async_close_clears_only_the_async_handle(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_client(client)
        _inject_fake_async_client(client)

        await client.aclose()
        assert client._aconnected is False
        assert client._aclient is None
        # The blocking handle is untouched by the async close.
        assert client._connected is True
        assert client._client is not None


# ==================== Async Setup (real connect path) ====================


@pytest.fixture
def reset_module_client(monkeypatch):
    """Drop the process-wide client so ``asetup`` builds a fresh one."""
    from src.core.database import valkey_client as valkey_module

    monkeypatch.setattr(valkey_module, "_default_client", None)
    return valkey_module


def _patch_async_glide_create(
    monkeypatch, fake, created: Optional[list] = None
) -> None:
    """Make ``glide.GlideClient.create`` hand back ``fake``.

    Only the transport is stubbed: ``aconnect`` still builds a real
    ``GlideClientConfiguration`` out of the real ``glide`` module, so the
    configuration path is exercised rather than paraphrased.
    """
    import glide

    async def _create(config):
        if created is not None:
            created.append(config)
        return fake

    monkeypatch.setattr(glide.GlideClient, "create", staticmethod(_create))


class TestValkeyAsyncSetup:
    """The real ``asetup`` -> ``aconnect`` path that opens the async handle."""

    @pytest.mark.asyncio
    async def test_asetup_opens_handle_and_async_ops_work(
        self, redis_config, reset_module_client, monkeypatch
    ):
        from src.core.database.valkey_client import asetup

        fake = FakeAsyncGlideClient()
        _patch_async_glide_create(monkeypatch, fake)

        client = await asetup()

        assert client is not None
        assert client is reset_module_client._default_client
        assert client._aconnected is True
        assert client._aclient is fake
        # The async handle is now live, so the a* surface is reachable.
        assert await client.aset("k", "v") is True
        assert await client.aget("k") == "v"
        assert await client.aping() is True

    @pytest.mark.asyncio
    async def test_asetup_is_idempotent(
        self, redis_config, reset_module_client, monkeypatch
    ):
        from src.core.database.valkey_client import asetup

        created: list = []
        _patch_async_glide_create(monkeypatch, FakeAsyncGlideClient(), created)

        first = await asetup()
        second = await asetup()

        assert first is second
        assert len(created) == 1  # a second call opens no second handle

    @pytest.mark.asyncio
    async def test_asetup_reuses_client_and_keeps_blocking_handle(
        self, redis_config, reset_module_client, monkeypatch
    ):
        """``asetup`` augments the client ``setup`` built; it must not replace it."""
        from src.core.database.valkey_client import ValkeyClient, asetup

        existing = ValkeyClient()
        _inject_fake_client(existing)  # the blocking handle setup() opens
        reset_module_client._default_client = existing
        _patch_async_glide_create(monkeypatch, FakeAsyncGlideClient())

        client = await asetup()

        assert client is existing
        assert client._connected is True
        assert client._client is not None  # blocking handle untouched
        assert client._aconnected is True

    @pytest.mark.asyncio
    async def test_asetup_returns_none_when_disabled(
        self, redis_disabled_config, reset_module_client, monkeypatch
    ):
        from src.core.database.valkey_client import asetup

        called: list = []
        _patch_async_glide_create(monkeypatch, FakeAsyncGlideClient(), called)

        assert await asetup() is None
        assert called == []  # never reached the transport

    @pytest.mark.asyncio
    async def test_asetup_degrades_when_connection_fails(
        self, redis_config, reset_module_client, monkeypatch
    ):
        import glide

        from src.core.database.valkey_client import asetup

        async def _boom(config):
            raise RuntimeError("no server")

        monkeypatch.setattr(glide.GlideClient, "create", staticmethod(_boom))

        assert await asetup() is None  # graceful, no raise
        assert reset_module_client._default_client._aconnected is False

    def test_is_available_true_with_only_async_handle(self, reset_module_client):
        from src.core.database.valkey_client import ValkeyClient, is_available

        client = ValkeyClient()
        _inject_fake_async_client(client)
        reset_module_client._default_client = client

        assert client._connected is False
        assert is_available() is True

    def test_is_available_false_without_client(self, reset_module_client):
        from src.core.database.valkey_client import is_available

        assert reset_module_client._default_client is None
        assert is_available() is False

    @pytest.mark.asyncio
    async def test_teardown_closes_both_handles(self, redis_config):
        from src.core.database.valkey_client import ValkeyClient

        client = ValkeyClient()
        _inject_fake_client(client)
        _inject_fake_async_client(client)

        # The two shutdown sites: the lifespan closes the async handle, then
        # PlexichatServer.cleanup closes the blocking one.
        await client.aclose()
        client.close()

        assert client._aconnected is False
        assert client._aclient is None
        assert client._connected is False
        assert client._client is None


class TestAsyncValkeyLifespanWiring:
    """The app-lifespan helpers that drive the setup/teardown above."""

    @pytest.mark.asyncio
    async def test_start_helper_awaits_asetup(self, monkeypatch):
        from src.api import app as app_module
        from src.core.database import valkey_client as valkey_module

        awaited: list = []

        async def _fake_asetup():
            awaited.append("asetup")
            return object()

        monkeypatch.setattr(valkey_module, "asetup", _fake_asetup)

        await app_module._start_async_valkey()

        assert awaited == ["asetup"]

    @pytest.mark.asyncio
    async def test_start_helper_swallows_setup_errors(self, monkeypatch):
        from src.api import app as app_module
        from src.core.database import valkey_client as valkey_module

        async def _boom():
            raise RuntimeError("unreachable")

        monkeypatch.setattr(valkey_module, "asetup", _boom)

        await app_module._start_async_valkey()  # no raise

    @pytest.mark.asyncio
    async def test_stop_helper_closes_the_async_handle(self, monkeypatch):
        from src.api import app as app_module
        from src.core.database import valkey_client as valkey_module

        class _StubClient:
            def __init__(self):
                self.closed = False

            async def aclose(self):
                self.closed = True

        stub = _StubClient()
        monkeypatch.setattr(valkey_module, "get_client", lambda: stub)

        await app_module._stop_async_valkey()

        assert stub.closed is True

    @pytest.mark.asyncio
    async def test_stop_helper_tolerates_absent_client(self, monkeypatch):
        from src.api import app as app_module
        from src.core.database import valkey_client as valkey_module

        monkeypatch.setattr(valkey_module, "get_client", lambda: None)

        await app_module._stop_async_valkey()  # no raise

    @pytest.mark.asyncio
    async def test_stop_helper_swallows_close_errors(self, monkeypatch):
        from src.api import app as app_module
        from src.core.database import valkey_client as valkey_module

        class _AngryClient:
            async def aclose(self):
                raise RuntimeError("boom")

        monkeypatch.setattr(valkey_module, "get_client", lambda: _AngryClient())

        await app_module._stop_async_valkey()  # no raise
