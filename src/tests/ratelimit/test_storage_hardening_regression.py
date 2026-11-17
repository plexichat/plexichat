"""Regressions for rate-limit storage hardening.

1. Valkey's blocking mutators must take the same distributed lock as the
   awaitable mutators. Otherwise the two paths do not serialise against each
   other and a concurrent write can clobber another worker's deduction.
2. MemoryStorage's mutating helpers must apply a TTL and honour the bucket
   cap on the write path, like every other write method and like their
   Valkey counterparts. Skipping either lets crafted distinct keys grow the
   dict without bound and never expire.
"""

import asyncio
from typing import Any

import pytest

import src.core.ratelimit.storage.valkey as valkey_module
from src.core.ratelimit.storage import MemoryStorage
from src.core.ratelimit.storage.valkey import ValkeyStorage

# All four mutators accept (key, field, value) positionally, which keeps the
# blocking/awaitable pairs in lockstep in the table below.
_MUTATOR_PAIRS = [
    ("increment", "aincrement"),
    ("get_and_set", "aget_and_set"),
    ("add_to_list", "aadd_to_list"),
    ("trim_list", "atrim_list"),
]


class _RecordingClient:
    """Fake Valkey client that records the unprefixed lock name each path uses."""

    def __init__(self) -> None:
        self.lock_calls: list[tuple[str, str]] = []

    def acquire_lock(self, key: str, timeout: float = 1.0) -> str:
        self.lock_calls.append(("sync", key))
        return "tok"

    def release_lock(self, key: str, token: str) -> None:
        pass

    def get_json(self, key: str) -> Any:
        return {}

    def set_json(self, key: str, state: Any, ttl: Any = None) -> None:
        pass

    async def aacquire_lock(self, key: str, timeout: float = 1.0) -> str:
        self.lock_calls.append(("async", key))
        return "tok"

    async def arelease_lock(self, key: str, token: str) -> None:
        pass

    async def aget_json(self, key: str) -> Any:
        return {}

    async def aset_json(self, key: str, state: Any, ttl: Any = None) -> None:
        pass


@pytest.mark.parametrize("sync_name,async_name", _MUTATOR_PAIRS)
def test_blocking_and_async_mutators_share_lock_namespace(
    monkeypatch: pytest.MonkeyPatch, sync_name: str, async_name: str
) -> None:
    """The two code paths must lock the same logical key, not/data key vs extlock."""
    client = _RecordingClient()
    monkeypatch.setattr(valkey_module, "get_client", lambda: client)
    monkeypatch.setattr(valkey_module, "is_available", lambda: True)

    storage = ValkeyStorage()
    getattr(storage, sync_name)("user:7", "field", 1)
    asyncio.run(getattr(storage, async_name)("user:7", "field", 1))

    assert [name for _, name in client.lock_calls] == [
        "extlock:user:7",
        "extlock:user:7",
    ]


def test_public_and_internal_locks_share_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public lock helper and the mutator path must agree on the lock name."""
    client = _RecordingClient()
    monkeypatch.setattr(valkey_module, "get_client", lambda: client)
    monkeypatch.setattr(valkey_module, "is_available", lambda: True)

    storage = ValkeyStorage()
    storage.acquire_lock("user:7")
    storage.increment("user:7", "field", 1)

    assert [name for _, name in client.lock_calls] == [
        "extlock:user:7",
        "extlock:user:7",
    ]


@pytest.mark.parametrize("method,args", [(name, ("field", 1)) for name, _ in _MUTATOR_PAIRS])
def test_memory_mutators_apply_expiring_ttl(
    rate_limit_clock: Any, method: str, args: tuple
) -> None:
    """Buckets created via mutators must be reaped by the TTL cleanup pass."""
    storage = MemoryStorage(cleanup_interval=10.0, max_buckets=100)

    getattr(storage, method)("victim", *args)
    assert storage.get_stats()["ttl_count"] == 1

    rate_limit_clock.advance(MemoryStorage.DEFAULT_BUCKET_TTL + 1)
    storage.get_bucket("trigger_cleanup")
    assert storage.get_bucket("victim") is None
    assert storage.get_stats()["bucket_count"] == 0


@pytest.mark.parametrize("method,args", [(name, ("field", 1)) for name, _ in _MUTATOR_PAIRS])
def test_memory_mutators_enforce_bucket_cap(method: str, args: tuple) -> None:
    """A flood of distinct keys through a mutator cannot grow the dict past the cap."""
    storage = MemoryStorage(cleanup_interval=10.0, max_buckets=5)

    for i in range(50):
        getattr(storage, method)(f"attacker:{i}", *args)

    stats = storage.get_stats()
    assert stats["bucket_count"] <= 5
    assert stats["ttl_count"] <= 5
