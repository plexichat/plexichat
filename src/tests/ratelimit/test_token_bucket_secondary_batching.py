"""Regression: token-bucket checks with hourly/daily quotas must batch their
storage work into a single locked round trip without weakening correctness.

Before the fused Valkey script a single check ran SEVEN client round trips on
the same bucket (lock/eval/unlock for the spend, then lock/get/set/unlock for
the secondary quota). The fused path keeps the SAME distributed lock (so it
still serialises with every other bucket mutation across nodes) but performs
the spend and the quota update in ONE atomic script: three round trips.

Pinned here:
  * the 7 -> 3 round-trip reduction and the exact call sequence;
  * decision + counter parity with the unfused MemoryStorage path;
  * fail-closed on an unacquirable lock and on a backend error;
  * fail-closed on the composing fallback when a write is silently skipped;
  * auxiliary bucket fields survive and two managers share the counter.
"""

from typing import Any, Dict, List

import pytest

import src.core.ratelimit.storage.valkey as valkey_module
from src.core.ratelimit.manager import RateLimitManager
from src.core.ratelimit.models import RateLimitAlgorithm, RateLimitConfig
from src.core.ratelimit.storage import MemoryStorage, ValkeyStorage

# Historical cost of one token-bucket + secondary-limit check on Valkey.
_LEGACY_ROUND_TRIPS = 7
_FUSED_ROUND_TRIPS = 3


class _CountingValkeyClient:
    """Faithful in-memory model that records EVERY client round trip."""

    def __init__(self, fail_lock: bool = False, fail_eval: bool = False) -> None:
        self.data: Dict[str, Dict[str, Any]] = {}
        self.calls: List[str] = []
        self.fail_lock = fail_lock
        self.fail_eval = fail_eval

    def _eval(self, script: str, keys: List[str], args: List[Any]) -> List[str]:
        fused = len(args) >= 8
        capacity, refill, cost, ttl, now = [float(a) for a in args[:5]]
        key = keys[0]
        state: Dict[str, Any] = dict(self.data.get(key) or {})
        tokens = float(state.get("tokens", capacity))
        last = float(state.get("last_update", now))
        tokens = min(capacity, tokens + max(0.0, now - last) * refill)
        allowed = tokens >= cost
        if allowed:
            tokens -= cost
        remaining = int(tokens)
        reset = (cost - tokens) / refill if not allowed else 0.0

        secondary = None
        if fused:
            hourly, daily, effective = [float(a) for a in args[5:8]]
            if allowed and hourly:
                hr = float(state.get("hourly_reset", now + 3600))
                hc = float(state.get("hourly_count", 0))
                if now >= hr:
                    hr, hc = now + 3600, 0
                if hc + cost <= hourly:
                    hc += cost
                else:
                    secondary = hr - now
                state["hourly_reset"], state["hourly_count"] = hr, hc
            if allowed and secondary is None and daily:
                dr = float(state.get("daily_reset", now + 86400))
                dc = float(state.get("daily_count", 0))
                if now >= dr:
                    dr, dc = now + 86400, 0
                if dc + cost <= daily:
                    dc += cost
                else:
                    secondary = dr - now
                state["daily_reset"], state["daily_count"] = dr, dc
            if secondary is not None:
                allowed = False
                reset = secondary
                tokens = min(effective, tokens + cost)

        state["tokens"] = tokens
        state["last_update"] = now
        self.data[key] = state
        result = [
            "true" if allowed else "false",
            str(remaining if fused else int(tokens)),
            str(reset),
        ]
        if fused:
            result.append("1")
        return result

    # -- sync client API -------------------------------------------------
    def acquire_lock(self, key: str, timeout: float = 1.0):
        self.calls.append("acquire_lock")
        return None if self.fail_lock else "tok"

    def release_lock(self, key: str, token: str) -> None:
        self.calls.append("release_lock")

    def eval_lua(self, script, keys=None, args=None):
        self.calls.append("eval_lua")
        if self.fail_eval:
            raise RuntimeError("backend down")
        return self._eval(script, keys or [], args or [])

    def get_json(self, key: str):
        self.calls.append("get_json")
        return dict(self.data[key]) if key in self.data else None

    def set_json(self, key: str, state: Dict[str, Any], ttl: Any = None) -> None:
        self.calls.append("set_json")
        self.data[key] = dict(state)

    def keys(self, pattern: str) -> List[str]:
        self.calls.append("keys")
        prefix = pattern[:-1]
        return [k for k in self.data if k.startswith(prefix)]

    def ttl(self, key: str) -> int:
        self.calls.append("ttl")
        return 86400

    def delete(self, *keys: str) -> int:
        self.calls.append("delete")
        return 0

    # -- async client API ------------------------------------------------
    async def aacquire_lock(self, key: str, timeout: float = 1.0):
        self.calls.append("aacquire_lock")
        return None if self.fail_lock else "tok"

    async def arelease_lock(self, key: str, token: str) -> None:
        self.calls.append("arelease_lock")

    async def aeval_lua(self, script, keys=None, args=None):
        self.calls.append("aeval_lua")
        if self.fail_eval:
            raise RuntimeError("backend down")
        return self._eval(script, keys or [], args or [])

    async def aget_json(self, key: str):
        self.calls.append("aget_json")
        return dict(self.data[key]) if key in self.data else None

    async def aset_json(self, key: str, state: Dict[str, Any], ttl: Any = None) -> None:
        self.calls.append("aset_json")
        self.data[key] = dict(state)

    async def akeys(self, pattern: str = "*") -> List[str]:
        self.calls.append("akeys")
        prefix = pattern[:-1]
        return [k for k in self.data if k.startswith(prefix)]

    async def attl(self, key: str) -> int:
        self.calls.append("attl")
        return 86400

    async def adelete(self, *keys: str) -> int:
        self.calls.append("adelete")
        return 0


def _wire(monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    monkeypatch.setattr(valkey_module, "get_client", lambda: client)
    monkeypatch.setattr(valkey_module, "is_available", lambda: True)
    # Keep the periodic key-scan out of call-count assertions.
    monkeypatch.setattr(ValkeyStorage, "_CAP_CHECK_INTERVAL", 1e9)


def _config(hourly_limit: int = 3) -> RateLimitConfig:
    return RateLimitConfig(
        requests=100,
        window_seconds=60.0,
        algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
        hourly_limit=hourly_limit,
    )


def _valkey_manager(storage: ValkeyStorage) -> RateLimitManager:
    return RateLimitManager(
        storage_backend=storage,
        route_configs={"GET /t": _config()},
        enable_global_limit=False,
    )


class TestRoundTripReduction:
    @pytest.mark.asyncio
    async def test_fused_check_uses_three_round_trips(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert _FUSED_ROUND_TRIPS < _LEGACY_ROUND_TRIPS
        client = _CountingValkeyClient()
        _wire(monkeypatch, client)
        manager = _valkey_manager(ValkeyStorage())

        # No principal: only the configured route bucket is evaluated, so the
        # call list is exactly one fused check (3 vs the legacy 7 round trips).
        result = await manager.acheck_rate_limit(route="GET /t")

        assert result.allowed is True
        assert client.calls == ["aacquire_lock", "aeval_lua", "arelease_lock"]
        assert len(client.calls) == _FUSED_ROUND_TRIPS

    def test_blocking_check_uses_three_round_trips(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _CountingValkeyClient()
        _wire(monkeypatch, client)
        manager = _valkey_manager(ValkeyStorage())

        assert manager.check_rate_limit(route="GET /t").allowed is True
        assert client.calls == ["acquire_lock", "eval_lua", "release_lock"]


class TestCorrectnessParity:
    @pytest.mark.asyncio
    async def test_fused_decisions_match_memory_backend(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _CountingValkeyClient()
        _wire(monkeypatch, client)
        valkey_manager = _valkey_manager(ValkeyStorage())
        memory_manager = RateLimitManager(
            storage_backend=MemoryStorage(),
            route_configs={"GET /t": _config()},
            enable_global_limit=False,
        )
        outcomes = []
        for _ in range(6):
            outcomes.append(
                await valkey_manager.acheck_rate_limit(user_id=1, route="GET /t")
            )
        expected = [
            await memory_manager.acheck_rate_limit(user_id=1, route="GET /t")
            for _ in range(6)
        ]
        assert [r.allowed for r in outcomes] == [r.allowed for r in expected]
        assert [r.allowed for r in outcomes] == [True, True, True, False, False, False]

    @pytest.mark.asyncio
    async def test_auxiliary_fields_survive_fused_spend(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _CountingValkeyClient()
        _wire(monkeypatch, client)
        storage = ValkeyStorage()
        await storage.aset_bucket("u:1", {"abuse_bytes": 4096})
        manager = _valkey_manager(storage)

        await manager.acheck_rate_limit(user_id=1, route="GET /t")

        assert client.data["ratelimit:u:1"]["abuse_bytes"] == 4096

    @pytest.mark.asyncio
    async def test_two_managers_share_one_fused_counter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Multi-node consistency: shared store, shared hourly quota."""
        client = _CountingValkeyClient()
        _wire(monkeypatch, client)
        a = _valkey_manager(ValkeyStorage())
        b = _valkey_manager(ValkeyStorage())

        outcomes = []
        for manager in (a, b, a, b):
            outcomes.append(
                (await manager.acheck_rate_limit(user_id=1, route="GET /t")).allowed
            )
        assert outcomes == [True, True, True, False]


class TestFusedFailClosed:
    @pytest.mark.asyncio
    async def test_unacquirable_lock_denies_without_writing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _CountingValkeyClient(fail_lock=True)
        _wire(monkeypatch, client)
        manager = _valkey_manager(ValkeyStorage())

        result = await manager.acheck_rate_limit(user_id=1, route="GET /t")

        assert result.allowed is False
        assert "aeval_lua" not in client.calls
        assert client.data == {}

    @pytest.mark.asyncio
    async def test_backend_error_denies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _CountingValkeyClient(fail_eval=True)
        _wire(monkeypatch, client)
        manager = _valkey_manager(ValkeyStorage())

        result = await manager.acheck_rate_limit(user_id=1, route="GET /t")

        assert result.allowed is False


class TestComposingFallback:
    @pytest.mark.asyncio
    async def test_fallback_still_applies_secondary_limits(self) -> None:
        """MemoryStorage has no fused primitive; default composition works."""
        manager = RateLimitManager(
            storage_backend=MemoryStorage(),
            route_configs={"GET /t": _config(hourly_limit=2)},
            enable_global_limit=False,
        )
        outcomes = [
            (await manager.acheck_rate_limit(user_id=1, route="GET /t")).allowed
            for _ in range(3)
        ]
        assert outcomes == [True, True, False]

    @pytest.mark.asyncio
    async def test_fallback_denies_when_write_is_silently_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        storage = MemoryStorage()
        calls = {"n": 0}

        async def noop_update(*args: Any, **kwargs: Any) -> None:
            calls["n"] += 1  # mutation callback never invoked -> no verdict

        monkeypatch.setattr(storage, "aupdate_bucket", noop_update)
        manager = RateLimitManager(
            storage_backend=storage,
            route_configs={"GET /t": _config()},
            enable_global_limit=False,
        )

        result = await manager.acheck_rate_limit(user_id=1, route="GET /t")

        assert result.allowed is False
        # user + route buckets both denied by the missing applied verdict.
        assert calls["n"] == 2
