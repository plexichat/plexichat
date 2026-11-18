"""Regressions for distributed rate-limit bucket state.

1. Valkey's token-bucket Lua script used to overwrite the stored bucket with a
   fresh ``{tokens, last_update}`` object. That wiped the hourly/daily quota
   counters a separate ``update_bucket`` call had just written, so those
   secondary limits never accumulated past one request when Valkey was the
   backend. The script (and the call that runs it) must preserve the rest of
   the bucket and serialise on the same distributed lock as every other
   bucket mutation.

2. Valkey bucket keys only carried a per-key TTL and had no count cap, unlike
   MemoryStorage (max_buckets) and DatabaseStorage (MAX_BUCKET_ROWS). A client
   rotating a caller-influenced identifier could grow the shared keyspace
   without bound inside one TTL window. The distributed store must bound its
   keys on the write path using the same key namespace it writes to.
"""

from typing import Any

import pytest

import src.core.ratelimit.storage.valkey as valkey_module
from src.core.ratelimit.manager import RateLimitManager
from src.core.ratelimit.models import RateLimitAlgorithm, RateLimitConfig
from src.core.ratelimit.storage import ValkeyStorage


class _ScriptValkeyClient:
    """In-memory stand-in that models the Lua token bucket's observable effect.

    ``eval_lua`` asserts the production script still preserves auxiliary
    bucket fields before emulating it, so reverting the script breaks the
    regression rather than silently passing against a permissive fake.
    """

    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}
        self.ttls: dict[str, Any] = {}
        self.locks: dict[str, str] = {}
        self.lock_calls: list[str] = []
        self.eval_calls: list[Any] = []

    def acquire_lock(self, key: str, timeout: float = 1.0):
        self.lock_calls.append(key)
        if key in self.locks:
            return None
        self.locks[key] = "tok"
        return "tok"

    def release_lock(self, key: str, token: str) -> None:
        self.locks.pop(key, None)

    def get_json(self, key: str):
        return dict(self.data[key]) if key in self.data else None

    def set_json(self, key: str, state: dict, ttl: Any = None) -> None:
        self.data[key] = dict(state)
        self.ttls[key] = ttl

    def eval_lua(self, script, keys=None, args=None):
        self.eval_calls.append((script, keys, args))
        # The script must decode into a table and write that SAME table back.
        assert "pcall(cjson.decode" in script, "script must decode stored state"
        assert "cjson.encode(state)" in script, "script must preserve other fields"
        assert "cjson.encode(res)" not in script, "script must not clobber the bucket"

        key = keys[0]
        capacity, refill_rate, cost, ttl, now = args
        raw = self.data.get(key)
        state = dict(raw) if isinstance(raw, dict) else {}
        tokens = float(state.get("tokens", capacity))
        last_update = float(state.get("last_update", now))
        elapsed = max(0.0, now - last_update)
        tokens = min(float(capacity), tokens + elapsed * refill_rate)
        allowed = tokens >= cost
        if allowed:
            tokens -= cost
        state["tokens"] = tokens
        state["last_update"] = now
        self.data[key] = state
        self.ttls[key] = ttl
        reset_after = (cost - tokens) / refill_rate if not allowed else 0.0
        return ["true" if allowed else "false", str(int(tokens)), str(reset_after)]

    def keys(self, pattern: str):
        prefix = pattern[:-1]
        return [key for key in self.data if key.startswith(prefix)]

    def ttl(self, key: str) -> int:
        return self.ttls.get(key, -1)

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.data:
                del self.data[key]
                self.ttls.pop(key, None)
                removed += 1
        return removed


class _AsyncScriptValkeyClient(_ScriptValkeyClient):
    """Awaitable wrapper around the same model."""

    async def aget_json(self, key):
        return self.get_json(key)

    async def aset_json(self, key, state, ttl=None):
        return self.set_json(key, state, ttl)

    async def aacquire_lock(self, key, timeout=5.0):
        return self.acquire_lock(key, timeout)

    async def arelease_lock(self, key, token):
        return self.release_lock(key, token)

    async def aeval_lua(self, script, keys=None, args=None):
        return self.eval_lua(script, keys, args)

    async def akeys(self, pattern="*"):
        return self.keys(pattern)

    async def attl(self, key):
        return self.ttl(key)

    async def adelete(self, *keys):
        return self.delete(*keys)


class _NoLockClient(_ScriptValkeyClient):
    def acquire_lock(self, key, timeout=1.0):
        self.lock_calls.append(key)
        return None


def _wire(monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    monkeypatch.setattr(valkey_module, "get_client", lambda: client)
    monkeypatch.setattr(valkey_module, "is_available", lambda: True)


class TestTokenBucketPreservesBucketState:
    def test_sync_spend_keeps_secondary_quota_counters(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _ScriptValkeyClient()
        _wire(monkeypatch, client)
        storage = ValkeyStorage()

        storage.update_bucket(
            "u:1", lambda state: state.update(hourly_count=7), ttl=86400
        )
        assert client.data["ratelimit:u:1"]["hourly_count"] == 7

        allowed, _, _ = storage.eval_token_bucket(
            "u:1", capacity=10, refill_rate=1.0, cost=1
        )
        assert allowed is True
        # The old two-field SET reset this to zero on every token spend.
        assert client.data["ratelimit:u:1"]["hourly_count"] == 7

    @pytest.mark.asyncio
    async def test_async_spend_keeps_secondary_quota_counters(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _AsyncScriptValkeyClient()
        _wire(monkeypatch, client)
        storage = ValkeyStorage()

        await storage.aupdate_bucket(
            "u:1", lambda state: state.update(daily_count=3), ttl=86400
        )
        allowed, _, _ = await storage.aeval_token_bucket(
            "u:1", capacity=10, refill_rate=1.0, cost=1
        )
        assert allowed is True
        assert client.data["ratelimit:u:1"]["daily_count"] == 3

    def test_eval_and_bucket_mutation_share_lock_namespace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _ScriptValkeyClient()
        _wire(monkeypatch, client)
        storage = ValkeyStorage()

        storage.eval_token_bucket("u:1", capacity=10, refill_rate=1.0, cost=1)
        storage.update_bucket("u:1", lambda state: state.update(x=1))

        assert client.lock_calls == ["extlock:u:1", "extlock:u:1"]
        assert client.locks == {}  # both released

    @pytest.mark.asyncio
    async def test_async_eval_and_bucket_mutation_share_lock_namespace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _AsyncScriptValkeyClient()
        _wire(monkeypatch, client)
        storage = ValkeyStorage()

        await storage.aeval_token_bucket("u:1", capacity=10, refill_rate=1.0, cost=1)
        await storage.aupdate_bucket("u:1", lambda state: state.update(x=1))

        assert client.lock_calls == ["extlock:u:1", "extlock:u:1"]
        assert client.locks == {}

    def test_unacquirable_lock_fails_closed_without_spending(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _NoLockClient()
        _wire(monkeypatch, client)
        storage = ValkeyStorage()

        outcome = storage.eval_token_bucket("u:1", capacity=10, refill_rate=1.0, cost=1)
        assert outcome == (False, 0, 60.0)
        assert client.eval_calls == []


class TestHourlyQuotaWithValkey:
    def test_hourly_limit_accumulates_through_manager(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end: the distributed-only quota bypass is closed."""
        client = _ScriptValkeyClient()
        _wire(monkeypatch, client)
        storage = ValkeyStorage()
        config = RateLimitConfig(
            requests=10,
            window_seconds=60.0,
            algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
            hourly_limit=2,
        )
        manager = RateLimitManager(
            storage_backend=storage,
            route_configs={"GET /t": config},
            enable_global_limit=False,
        )

        outcomes = [
            manager.check_rate_limit(user_id=1, route="GET /t").allowed
            for _ in range(3)
        ]
        assert outcomes == [True, True, False]


class TestBoundedValkeyKeyspace:
    def test_write_path_caps_distinct_data_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _ScriptValkeyClient()
        _wire(monkeypatch, client)
        monkeypatch.setattr(ValkeyStorage, "MAX_BUCKET_KEYS", 4)
        monkeypatch.setattr(ValkeyStorage, "_CAP_CHECK_INTERVAL", 0)
        storage = ValkeyStorage()

        for i in range(40):
            storage.set_bucket(f"attacker:{i}", {"n": i})

        assert len(client.data) <= 4

    @pytest.mark.asyncio
    async def test_async_write_path_caps_distinct_data_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _AsyncScriptValkeyClient()
        _wire(monkeypatch, client)
        monkeypatch.setattr(ValkeyStorage, "MAX_BUCKET_KEYS", 4)
        monkeypatch.setattr(ValkeyStorage, "_CAP_CHECK_INTERVAL", 0)
        storage = ValkeyStorage()

        for i in range(40):
            await storage.aset_bucket(f"attacker:{i}", {"n": i})

        assert len(client.data) <= 4

    def test_cap_uses_only_the_data_namespace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _ScriptValkeyClient()
        _wire(monkeypatch, client)
        monkeypatch.setattr(ValkeyStorage, "MAX_BUCKET_KEYS", 4)
        monkeypatch.setattr(ValkeyStorage, "_CAP_CHECK_INTERVAL", 0)
        storage = ValkeyStorage()

        # A lock key that the data-key scan must never select.
        client.data["lock:extlock:u:1"] = {"token": "tok"}
        for i in range(20):
            storage.set_bucket(f"attacker:{i}", {"n": i})

        assert "lock:extlock:u:1" in client.data
        assert len([k for k in client.data if k.startswith("ratelimit:")]) <= 4
