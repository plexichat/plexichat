"""Regressions: Valkey key-cap pruning must not run under the bucket lock.

``ValkeyStorage`` bounds its distributed keyspace by SCANning the data
namespace and deleting soonest-expiring keys when the cap is exceeded. That
prune is O(N) (one SCAN plus one TTL round trip per key). Historically it was
invoked from *inside* every locked read-modify-write / token spend, i.e. while
the ``extlock:<key>`` distributed lock was held:

* the 30s lock TTL could expire during a large prune, letting a second worker
  acquire the same lock and double-spend the bucket (fail-open on lock loss);
* the O(N) round trips serialised behind a per-bucket lock, inflating tail
  latency and lock contention on the exact write path the cap protects.

The prune stays best-effort and bounded, but it must happen after the bucket
lock is released. These tests fail against the old in-lock behaviour and pass
once the prune is moved outside the critical section.
"""

from typing import Any, Dict, List, Optional

import pytest

import src.core.ratelimit.storage.valkey as valkey_module
from src.core.ratelimit.storage import ValkeyStorage


class _ScopeTrackingClient:
    """Fake Valkey client that records whether a bucket lock is held when the
    key-cap prune starts a SCAN.

    Lock ownership is modelled explicitly (``self.locks``), so the test can
    distinguish a prune that runs inside the critical section from one that
    runs after ``release_lock``.
    """

    def __init__(self) -> None:
        self.data: Dict[str, Dict[str, Any]] = {}
        self.ttls: Dict[str, int] = {}
        self.locks: Dict[str, str] = {}
        # One entry per cap prune: True if any extlock was held at that moment.
        self.cap_prune_lock_held: List[bool] = []
        self.cap_prune_scans = 0

    # -- distributed lock --------------------------------------------------
    def acquire_lock(self, key: str, timeout: float = 1.0) -> Optional[str]:
        if key in self.locks:
            return None
        self.locks[key] = "tok"
        return "tok"

    def release_lock(self, key: str, token: str) -> None:
        self.locks.pop(key, None)

    # -- data primitives ---------------------------------------------------
    def get_json(self, key: str) -> Optional[Dict[str, Any]]:
        value = self.data.get(key)
        return dict(value) if value is not None else None

    def set_json(self, key: str, state: Dict[str, Any], ttl: Any = None) -> None:
        self.data[key] = dict(state)
        self.ttls[key] = int(ttl) if ttl is not None else 86400

    def eval_lua(self, script, keys=None, args=None):
        # Minimal token-bucket emulation; the prune-scope assertion does not
        # depend on the exact tokens maths, only that the spend completed.
        capacity, refill_rate, cost, ttl, now = args[:5]
        key = keys[0]
        state = dict(self.data.get(key, {}))
        tokens = float(state.get("tokens", capacity))
        last_update = float(state.get("last_update", now))
        tokens = min(
            float(capacity), tokens + max(0.0, now - last_update) * refill_rate
        )
        allowed = tokens >= cost
        if allowed:
            tokens -= cost
        else:
            tokens = min(float(capacity), tokens + cost)  # no-op rollback emulation
        state["tokens"] = tokens
        state["last_update"] = now
        self.data[key] = state
        self.ttls[key] = int(ttl)
        return ["true" if allowed else "false", str(int(tokens)), "0.0"]

    # -- key-cap prune primitives (the code under test) --------------------
    def keys(self, pattern: str) -> List[str]:
        self.cap_prune_scans += 1
        self.cap_prune_lock_held.append(
            any(k.startswith("extlock:") for k in self.locks)
        )
        prefix = pattern[:-1] if pattern.endswith("*") else pattern
        return [k for k in self.data if k.startswith(prefix)]

    def ttl(self, key: str) -> int:
        return self.ttls.get(key, 86400)

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.data:
                del self.data[key]
                self.ttls.pop(key, None)
                removed += 1
        return removed


class _AsyncScopeTrackingClient(_ScopeTrackingClient):
    async def aacquire_lock(self, key, timeout=5.0):
        return self.acquire_lock(key, timeout)

    async def arelease_lock(self, key, token):
        self.release_lock(key, token)

    async def aget_json(self, key):
        return self.get_json(key)

    async def aset_json(self, key, state, ttl=None):
        self.set_json(key, state, ttl)

    async def aeval_lua(self, script, keys=None, args=None):
        return self.eval_lua(script, keys, args)

    async def akeys(self, pattern="*"):
        return self.keys(pattern)

    async def attl(self, key):
        return self.ttl(key)

    async def adelete(self, *keys):
        return self.delete(*keys)


def _wire(monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    monkeypatch.setattr(valkey_module, "get_client", lambda: client)
    monkeypatch.setattr(valkey_module, "is_available", lambda: True)
    monkeypatch.setattr(ValkeyStorage, "_CAP_CHECK_INTERVAL", 0)
    monkeypatch.setattr(ValkeyStorage, "MAX_BUCKET_KEYS", 4)


def _fill(client: Any, count: int = 20) -> None:
    for i in range(count):
        client.data[f"ratelimit:attacker:{i}"] = {"n": i}
        client.ttls[f"ratelimit:attacker:{i}"] = 86400 + i


class TestCapPruneOutsideBucketLock:
    def test_sync_counter_mutator_prunes_after_lock_release(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _ScopeTrackingClient()
        _wire(monkeypatch, client)
        storage = ValkeyStorage()
        _fill(client)

        client.cap_prune_lock_held.clear()
        storage.increment("user:1", "hits", 1)

        assert client.cap_prune_lock_held, "expected the write path to prune"
        assert client.cap_prune_lock_held[-1] is False, (
            "key-cap prune ran while extlock:<key> was held"
        )
        assert client.locks == {}, "bucket lock was not released"
        assert len(client.data) <= ValkeyStorage.MAX_BUCKET_KEYS

    def test_sync_token_spend_prunes_after_lock_release(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _ScopeTrackingClient()
        _wire(monkeypatch, client)
        storage = ValkeyStorage()
        _fill(client)

        client.cap_prune_lock_held.clear()
        storage.eval_token_bucket("user:1", 10, 1.0, 1)

        assert client.cap_prune_lock_held
        assert client.cap_prune_lock_held[-1] is False
        assert client.locks == {}

    @pytest.mark.asyncio
    async def test_async_counter_mutator_prunes_after_lock_release(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _AsyncScopeTrackingClient()
        _wire(monkeypatch, client)
        storage = ValkeyStorage()
        _fill(client)

        client.cap_prune_lock_held.clear()
        await storage.aincrement("user:1", "hits", 1)

        assert client.cap_prune_lock_held
        assert client.cap_prune_lock_held[-1] is False
        assert client.locks == {}
        assert len(client.data) <= ValkeyStorage.MAX_BUCKET_KEYS

    @pytest.mark.asyncio
    async def test_async_update_bucket_prunes_after_lock_release(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _AsyncScopeTrackingClient()
        _wire(monkeypatch, client)
        storage = ValkeyStorage()
        _fill(client)

        client.cap_prune_lock_held.clear()
        await storage.aupdate_bucket("user:1", lambda state: state.update(x=1))

        assert client.cap_prune_lock_held
        assert client.cap_prune_lock_held[-1] is False
        assert client.locks == {}
