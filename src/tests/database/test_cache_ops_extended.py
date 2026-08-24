"""Extended targeted tests for the cache layer (mem tier, valkey fallback, serialization)."""

import dataclasses
import enum
import fnmatch
import time

import pytest

from src.core.database.cache import operations
from src.core.database.cache.base import (
    mem_cache_get,
    mem_cache_set,
    mem_cache_delete,
    mem_cache_clear_pattern,
    default_cache_ttl,
)
from src.core.database.cache.serialization import (
    ensure_serializable,
    reconstruct_object,
    register_reconstructable_type,
    generate_cache_key,
)
from src.core.database.valkey_client import ValkeyOperationError


@pytest.fixture(autouse=True)
def _clear_mem_cache():
    """Isolate the module-level in-memory cache between tests."""
    from src.core.database.cache.base import _mem_cache

    _mem_cache.clear()
    yield
    _mem_cache.clear()


class FakeClock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(time, "time", c)
    return c


class FakeValkey:
    """Minimal stand-in for the valkey client used by cache operations."""

    def __init__(self):
        self.store = {}
        self.deleted = []
        self.keys_calls = 0

    def get_json(self, key):
        return self.store.get(key)

    def set_json(self, key, value, ttl=None):
        self.store[key] = value

    def delete(self, *keys):
        self.deleted.extend(keys)
        for k in keys:
            self.store.pop(k, None)
        return len(keys)

    def keys(self, pattern):
        self.keys_calls += 1
        return [k for k in self.store if fnmatch.fnmatch(k, pattern)]

    def mget_json(self, keys):
        return {k: self.store.get(k) for k in keys}


@pytest.fixture
def valkey_off(monkeypatch):
    monkeypatch.setattr(operations, "get_client", lambda: None)
    monkeypatch.setattr(operations, "is_available", lambda: False)


@pytest.fixture
def valkey_on(monkeypatch):
    client = FakeValkey()
    monkeypatch.setattr(operations, "get_client", lambda: client)
    monkeypatch.setattr(operations, "is_available", lambda: True)
    return client


# ---------------------------------------------------------------------------
# In-memory tier
# ---------------------------------------------------------------------------


def test_mem_set_get():
    mem_cache_set("k", "v", 60)
    assert mem_cache_get("k") == "v"


def test_mem_expiry(clock):
    mem_cache_set("k", "v", 60)
    clock.advance(61)
    assert mem_cache_get("k") is None


def test_mem_expiry_removes_entry(clock):
    mem_cache_set("k", "v", 60)
    clock.advance(61)
    mem_cache_get("k")
    from src.core.database.cache.base import _mem_cache

    assert "k" not in _mem_cache


def test_mem_ttl_boundary_exact(clock):
    mem_cache_set("k", "v", 60)
    clock.advance(60)  # expiry == now -> expired (expiry > now is required)
    assert mem_cache_get("k") is None


def test_mem_ttl_boundary_before(clock):
    mem_cache_set("k", "v", 60)
    clock.advance(59.999)
    assert mem_cache_get("k") == "v"


def test_mem_delete():
    mem_cache_set("k", "v", 60)
    assert mem_cache_delete("k") is True
    assert mem_cache_delete("k") is False
    assert mem_cache_get("k") is None


@pytest.mark.parametrize(
    ("pattern", "keys", "expected_matches"),
    [
        ("presence:*", ["presence:1", "presence:2", "session:1"], 2),
        ("*:1", ["presence:1", "presence:2", "session:1"], 2),
        ("nope*", ["presence:1"], 0),
        ("*", ["a", "b"], 2),
    ],
)
def test_mem_clear_pattern(pattern, keys, expected_matches):
    for i, k in enumerate(keys):
        mem_cache_set(k, i, 60)
    assert mem_cache_clear_pattern(pattern) == expected_matches


# ---------------------------------------------------------------------------
# cache_set / cache_get with valkey unavailable -> mem tier
# ---------------------------------------------------------------------------


def test_set_get_mem_fallback(valkey_off):
    assert operations.cache_set("k", {"a": 1}) is True
    assert operations.cache_get("k") == {"a": 1}


def test_get_missing_returns_none(valkey_off):
    assert operations.cache_get("missing") is None


def test_cache_get_serves_mem_on_valkey_miss(valkey_on):
    # Write only reached the mem tier (e.g. a previous valkey write failure):
    # cache_get must still serve the L1 copy even though valkey is healthy.
    mem_cache_set("stale-key", "mem-value", 60)
    assert operations.cache_get("stale-key") == "mem-value"


def test_cache_get_prefers_valkey_on_hit(valkey_on):
    client = valkey_on
    client.store["k"] = {"source": "valkey"}
    mem_cache_set("k", {"source": "mem"}, 60)
    assert operations.cache_get("k") == {"source": "valkey"}


def test_cache_delete_both_tiers(valkey_on):
    client = valkey_on
    client.store["k"] = 1
    mem_cache_set("k", 1, 60)
    assert operations.cache_delete("k") is True
    assert client.deleted == ["k"]
    assert operations.cache_get("k") is None


def test_invalidate_cached_alias(valkey_off):
    operations.cache_set("k", 1)
    assert operations.invalidate_cached("k") is True
    assert operations.cache_get("k") is None


def test_cache_set_error_falls_back_to_mem(monkeypatch, valkey_on):
    class Broken(FakeValkey):
        def set_json(self, key, value, ttl=None):
            raise ValkeyOperationError("boom")

    monkeypatch.setattr(operations, "get_client", lambda: Broken())
    assert operations.cache_set("k", 42) is True
    assert operations.cache_get("k") == 42


# ---------------------------------------------------------------------------
# cache_get_many
# ---------------------------------------------------------------------------


def test_get_many_empty(valkey_on):
    assert operations.cache_get_many([]) == {}


def test_get_many_mem_fallback(valkey_off):
    operations.cache_set("a", 1)
    operations.cache_set("b", 2)
    assert operations.cache_get_many(["a", "b", "c"]) == {"a": 1, "b": 2}


def test_get_many_valkey_mget(valkey_on):
    client = valkey_on
    client.store["x"] = {"n": 1}
    client.store["y"] = {"n": 2}
    assert operations.cache_get_many(["x", "y", "z"]) == {"x": {"n": 1}, "y": {"n": 2}}


def test_get_many_fallback_on_error(monkeypatch, valkey_on):
    class Broken(FakeValkey):
        def mget_json(self, keys):
            raise ValkeyOperationError("boom")

    # One stable client instance: cache_set writes it, the mget path on the
    # same instance raises, and the cache_get fallback loop must then serve
    # the value back through get_json/mem rather than return an empty dict.
    broken = Broken()
    monkeypatch.setattr(operations, "get_client", lambda: broken)
    operations.cache_set("q", 7)
    assert operations.cache_get_many(["q"]) == {"q": 7}


# ---------------------------------------------------------------------------
# invalidate_pattern
# ---------------------------------------------------------------------------


def test_invalidate_pattern_valkey_and_mem(valkey_on):
    client = valkey_on
    client.store["presence:1"] = 1
    client.store["presence:2"] = 2
    client.store["other"] = 3
    mem_cache_set("presence:9", 9, 60)
    n = operations.invalidate_pattern("presence:*")
    assert n == 3  # 2 valkey + 1 mem
    assert operations.cache_get("other") == 3
    assert operations.cache_get("presence:1") is None
    assert operations.cache_get("presence:9") is None


def test_invalidate_pattern_mem_only(valkey_off):
    operations.cache_set("sess:1", 1)
    assert operations.invalidate_pattern("sess:*") == 1
    assert operations.cache_get("sess:1") is None


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class _Color(enum.Enum):
    RED = "red"
    BLUE = "blue"


@dataclasses.dataclass
class _Point:
    x: int
    y: int


class _Duck:
    def __init__(self, name):
        self.name = name

    def model_dump(self):
        return {"name": self.name}


class _OldStyle:
    def __init__(self, n):
        self.n = n

    def dict(self):
        return {"n": self.n}


# Reconstruction is allowlist-only (untrusted cache payloads must never be
# able to instantiate arbitrary classes), so the local round-trip fixtures
# must be registered explicitly.
register_reconstructable_type(_Color, _Point, _Duck, _OldStyle)


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        0,
        42,
        -1,
        3.14,
        "string",
        "",
        [1, 2, 3],
        {"a": 1, "b": [2, 3]},
        ("tuple", 1),  # tuples become lists
        {1, 2, 3},  # sets become lists
    ],
)
def test_ensure_serializable_primitives(value):
    out = ensure_serializable(value)
    if isinstance(value, tuple):
        assert out == list(value)
    elif isinstance(value, set):
        assert sorted(out) == sorted(value)
    else:
        assert out == value


def test_serializable_enum_becomes_value():
    assert ensure_serializable(_Color.RED) == "red"


def test_serializable_dataclass_round_trip():
    data = ensure_serializable(_Point(1, 2))
    assert data["x"] == 1
    assert data["y"] == 2
    assert "__type__" in data
    back = reconstruct_object(data)
    assert isinstance(back, _Point)
    assert back.x == 1 and back.y == 2


def test_serializable_model_dump_round_trip():
    data = ensure_serializable(_Duck("ducky"))
    assert data["name"] == "ducky"
    assert "__type__" in data
    back = reconstruct_object(data)
    assert isinstance(back, _Duck)
    assert back.name == "ducky"


def test_serializable_old_style_dict_round_trip():
    data = ensure_serializable(_OldStyle(7))
    assert data["n"] == 7
    assert "__type__" in data
    back = reconstruct_object(data)
    assert isinstance(back, _OldStyle)
    assert back.n == 7


def test_serializable_nested_round_trip():
    data = ensure_serializable({"pt": _Point(3, 4), "color": _Color.BLUE})
    back = reconstruct_object(data)
    assert isinstance(back["pt"], _Point)
    assert back["pt"].y == 4
    assert back["color"] == "blue"


def test_reconstruct_unknown_type_returns_dict():
    data = {"__type__": "no.such.module.Cls", "a": 1}
    assert reconstruct_object(data) == {"a": 1}


def test_reconstruct_enum_branch():
    data = {"__type__": f"{_Color.__module__}.{_Color.__qualname__}", "value": "red"}
    out = reconstruct_object(data)
    # Enum reconstruction returns the enum member (round-trip fidelity).
    assert out is _Color.RED


def test_reconstruct_plain_dict_no_type():
    assert reconstruct_object({"a": {"b": 1}}) == {"a": {"b": 1}}


def test_reconstruct_list():
    assert reconstruct_object([{"a": 1}, 2]) == [{"a": 1}, 2]


# ---------------------------------------------------------------------------
# generate_cache_key
# ---------------------------------------------------------------------------


def test_key_stable_for_same_args():
    assert generate_cache_key("pfx", 1, "two", k=3) == generate_cache_key(
        "pfx", 1, "two", k=3
    )


def test_key_differs_for_diff_args():
    assert generate_cache_key("pfx", 1) != generate_cache_key("pfx", 2)


def test_key_kwargs_sorted():
    assert generate_cache_key("pfx", a=1, b=2) == generate_cache_key("pfx", b=2, a=1)


def test_key_dict_hashed():
    k1 = generate_cache_key("pfx", {"a": 1, "b": 2})
    k2 = generate_cache_key("pfx", {"b": 2, "a": 1})
    k3 = generate_cache_key("pfx", {"a": 1, "b": 3})
    assert k1 == k2
    assert k1 != k3


def test_key_includes_prefix():
    assert generate_cache_key("user", 1).startswith("user:")


def test_key_for_object_like_user():
    class User:
        user_id = 5

    assert "User:5" in generate_cache_key("k", User())


# ---------------------------------------------------------------------------
# default_ttl
# ---------------------------------------------------------------------------


def test_default_cache_ttl_is_int():
    ttl = default_cache_ttl()
    assert isinstance(ttl, int)
    assert ttl > 0


def test_cache_stats_updated(valkey_on):
    operations.cache_get("missing-key")
    stats = operations.get_cache_stats()
    assert stats["misses"] >= 1
