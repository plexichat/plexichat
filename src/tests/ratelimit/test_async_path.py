"""
Async rate-limit path tests.

The rate limiter exposes two interfaces over one backend: the blocking
methods (used off the loop) and the ``a*`` methods (used by the ASGI
middleware, the route decorators and async handlers). These tests pin the
async interface down: it must be reachable, it must agree with the blocking
path, and the storage backends that can do better than a thread offload must
actually use their native async client.
"""

import asyncio
import fnmatch
from typing import Any, Callable, Dict, Optional

import pytest
from starlette.requests import Request

from src.core.ratelimit.manager import RateLimitManager
from src.core.ratelimit.models import (
    BucketType,
    RateLimitAlgorithm,
    RateLimitConfig,
)
from src.core.ratelimit.storage import MemoryStorage, RateLimitStorage


def _manager(
    limit: int = 3,
    algorithm: RateLimitAlgorithm = RateLimitAlgorithm.TOKEN_BUCKET,
    storage: Optional[RateLimitStorage] = None,
    **kwargs,
) -> RateLimitManager:
    """Manager with a single controlled route and no global bucket."""
    cfg = RateLimitConfig(
        requests=limit,
        window_seconds=60.0,
        burst=0,
        algorithm=algorithm,
    )
    return RateLimitManager(
        storage_backend=storage or MemoryStorage(),
        route_configs={"GET /x": cfg},
        enable_global_limit=False,
        **kwargs,
    )


@pytest.fixture
def rl_module_state():
    """Save/restore the global ratelimit module state around a test."""
    import src.core.ratelimit as rl

    prev_manager = rl._manager
    prev_complete = rl._setup_complete
    try:
        yield rl
    finally:
        rl._manager = prev_manager
        rl._setup_complete = prev_complete


class TestManagerAsyncParity:
    """The awaitable manager must agree with the blocking one."""

    @pytest.mark.asyncio
    async def test_token_bucket_allow_then_deny(self):
        manager = _manager(limit=3)

        for _ in range(3):
            result = await manager.acheck_rate_limit(user_id=1, route="GET /x")
            assert result.allowed is True

        denied = await manager.acheck_rate_limit(user_id=1, route="GET /x")
        assert denied.allowed is False
        assert denied.remaining == 0
        assert denied.retry_after is not None

    @pytest.mark.asyncio
    async def test_matches_blocking_path_bucket_for_bucket(self):
        sync_manager = _manager(limit=3)
        async_manager = _manager(limit=3)

        for _ in range(5):
            sync_result = sync_manager.check_rate_limit(user_id=7, route="GET /x")
            async_result = await async_manager.acheck_rate_limit(
                user_id=7, route="GET /x"
            )

            assert async_result.allowed == sync_result.allowed
            assert async_result.remaining == sync_result.remaining
            assert async_result.headers.limit == sync_result.headers.limit
            # retry_after is wall-clock derived, so the two runs differ by
            # microseconds; compare the value, not the timestamp.
            assert async_result.retry_after == pytest.approx(
                sync_result.retry_after, abs=0.5
            )

        # The limiter actually engaged (otherwise parity is vacuous).
        assert sync_result.allowed is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "algorithm",
        [
            RateLimitAlgorithm.FIXED_WINDOW,
            RateLimitAlgorithm.SLIDING_WINDOW,
            RateLimitAlgorithm.LEAKY_BUCKET,
        ],
    )
    async def test_legacy_algorithms_match_blocking_path(self, algorithm):
        sync_manager = _manager(limit=3, algorithm=algorithm)
        async_manager = _manager(limit=3, algorithm=algorithm)

        for _ in range(5):
            sync_result = sync_manager.check_rate_limit(user_id=11, route="GET /x")
            async_result = await async_manager.acheck_rate_limit(
                user_id=11, route="GET /x"
            )
            assert async_result.allowed == sync_result.allowed
            assert async_result.remaining == sync_result.remaining

    @pytest.mark.asyncio
    async def test_secondary_limit_rolls_back_primary_token(self):
        """A denied hourly limit must not burn the primary token.

        The rollback shows up in storage (the bucket is refilled), not in
        ``remaining`` -- that is reported from the pre-rollback primary
        evaluation, so it still reflects the consumed token.
        """
        cfg = RateLimitConfig(
            requests=10,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
            hourly_limit=1,
        )
        manager = RateLimitManager(
            storage_backend=MemoryStorage(),
            route_configs={"GET /x": cfg},
            enable_global_limit=False,
        )

        def token_buckets():
            return [
                state
                for state in manager._storage._buckets.values()
                if "tokens" in state
            ]

        first = await manager.acheck_rate_limit(user_id=5, route="GET /x")
        assert first.allowed is True

        after_first = token_buckets()
        assert len(after_first) == 1
        tokens_after_first = after_first[0]["tokens"]

        second = await manager.acheck_rate_limit(user_id=5, route="GET /x")
        assert second.allowed is False
        assert second.remaining == first.remaining - 1

        # The denied request rolled its token back, so the primary bucket is
        # exactly where the one allowed request left it (modulo refill).
        after_second = token_buckets()
        assert len(after_second) == 1
        assert after_second[0]["tokens"] == pytest.approx(tokens_after_first, abs=0.01)

    @pytest.mark.asyncio
    async def test_internal_bypass_is_honoured(self):
        manager = _manager(limit=1)
        result = await manager.acheck_rate_limit(
            user_id=1, route="GET /x", is_internal=True
        )
        assert result.allowed is True
        assert result.headers.scope == "bypass"

    @pytest.mark.asyncio
    async def test_custom_usage_roundtrip_and_reset(self):
        manager = _manager()
        assert await manager.aincrement_custom_usage("c:1", "count", 1, ttl=60) == 1
        assert await manager.aincrement_custom_usage("c:1", "count", 1, ttl=60) == 2

        info = await manager.aget_bucket_info("c:1")
        assert info is not None
        assert info.request_count == 0

        await manager.areset_all()
        assert await manager.aincrement_custom_usage("c:1", "count", 0, ttl=60) == 0

    @pytest.mark.asyncio
    async def test_areset_user_uses_exact_segment_match(self):
        manager = _manager()
        await manager.aincrement_custom_usage("u:1:route:GET /x", "count", 1, ttl=60)
        await manager.aincrement_custom_usage("u:10:route:GET /x", "count", 1, ttl=60)

        await manager.areset_user(1)

        # user 1 is gone, user 10 is untouched (the old substring match
        # deleted users 10..19 as a side effect).
        assert (
            await manager.aincrement_custom_usage(
                "u:1:route:GET /x", "count", 0, ttl=60
            )
            == 0
        )
        assert (
            await manager.aincrement_custom_usage(
                "u:10:route:GET /x", "count", 0, ttl=60
            )
            == 1
        )


class _FakeAsyncValkey:
    """Minimal awaitable stand-in for the GLIDE client."""

    def __init__(self):
        self.data = {}
        self.locks = {}
        self.eval_result = ["true", "9", "0"]
        self.eval_error = None

    async def aget_json(self, key):
        return self.data.get(key)

    async def aset_json(self, key, value, ttl=None):
        self.data[key] = dict(value)
        return True

    async def adelete(self, *keys):
        removed = 0
        for key in keys:
            if key in self.data:
                del self.data[key]
                removed += 1
        return removed

    async def akeys(self, pattern="*"):
        return [k for k in self.data if fnmatch.fnmatch(k, pattern)]

    async def aacquire_lock(self, key, timeout=5.0):
        if key in self.locks:
            return None
        self.locks[key] = "token"
        return "token"

    async def arelease_lock(self, key, token):
        self.locks.pop(key, None)
        return True

    async def aeval_lua(self, script, keys=None, args=None):
        if self.eval_error is not None:
            raise self.eval_error
        return list(self.eval_result)


@pytest.fixture
def valkey_storage(monkeypatch):
    """A ValkeyStorage wired to the fake async client."""
    import src.core.ratelimit.storage.valkey as valkey_module
    from src.core.ratelimit.storage import ValkeyStorage

    fake = _FakeAsyncValkey()
    monkeypatch.setattr(valkey_module, "get_client", lambda: fake)
    monkeypatch.setattr(valkey_module, "is_available", lambda: True)
    return ValkeyStorage(), fake


class TestValkeyStorageAsync:
    """The Valkey backend must drive the async client, not a thread offload."""

    @pytest.mark.asyncio
    async def test_bucket_crud_roundtrip(self, valkey_storage):
        storage, _ = valkey_storage

        assert await storage.aget_bucket("b") is None
        await storage.aset_bucket("b", {"tokens": 2}, ttl=30)
        assert await storage.aget_bucket("b") == {"tokens": 2}

        assert await storage.adelete_bucket("b") is True
        assert await storage.aget_bucket("b") is None

    @pytest.mark.asyncio
    async def test_keys_by_prefix_and_delete(self, valkey_storage):
        storage, _ = valkey_storage
        await storage.aset_bucket("p:1", {"a": 1}, ttl=30)
        await storage.aset_bucket("p:2", {"a": 2}, ttl=30)
        await storage.aset_bucket("other", {"a": 3}, ttl=30)

        assert sorted(await storage.aget_keys_by_prefix("p:")) == [
            "ratelimit:p:1",
            "ratelimit:p:2",
        ]
        assert await storage.adelete_by_prefix("p:") == 2
        assert await storage.aget_bucket("p:1") is None

        await storage.aclear_all()
        assert await storage.aget_keys_by_prefix("") == []

    @pytest.mark.asyncio
    async def test_increment_is_a_single_locked_read_modify_write(self, valkey_storage):
        storage, fake = valkey_storage

        assert await storage.aincrement("ctr", "count", 1) == 1
        assert await storage.aincrement("ctr", "count", 4) == 5
        assert fake.data["ratelimit:ctr"]["count"] == 5
        # The lock is released even on the success path.
        assert fake.locks == {}

    @pytest.mark.asyncio
    async def test_get_and_set_returns_previous(self, valkey_storage):
        storage, _ = valkey_storage
        assert await storage.aget_and_set("b", "f", "v1", default=None) is None
        assert await storage.aget_and_set("b", "f", "v2", default=None) == "v1"

    @pytest.mark.asyncio
    async def test_list_helpers_bound_and_trim(self, valkey_storage):
        storage, _ = valkey_storage
        for i in range(4):
            await storage.aadd_to_list("lst", "items", i, max_size=3)

        assert fake_items(await storage.aget_bucket("lst")) == [1, 2, 3]
        removed = await storage.atrim_list("lst", "items", 2)
        assert removed == 1

    @pytest.mark.asyncio
    async def test_eval_token_bucket_reads_lua_result(self, valkey_storage):
        storage, fake = valkey_storage
        fake.eval_result = ["false", "0", "12.5"]

        allowed, remaining, reset_after = await storage.aeval_token_bucket(
            "tb", capacity=10, refill_rate=1.0, cost=1
        )
        assert allowed is False
        assert remaining == 0
        assert reset_after == 12.5

    @pytest.mark.asyncio
    async def test_eval_fails_closed_on_backend_error(
        self, valkey_storage, monkeypatch
    ):
        storage, fake = valkey_storage
        fake.eval_error = RuntimeError("valkey down")
        monkeypatch.delenv("PLEXICHAT_REQUIRE_FAIL_CLOSED", raising=False)

        allowed, remaining, reset_after = await storage.aeval_token_bucket(
            "tb", capacity=10, refill_rate=1.0, cost=1
        )
        assert allowed is False
        assert remaining == 0
        assert reset_after == 60.0

    @pytest.mark.asyncio
    async def test_unavailable_backend_degrades_without_raising(self, monkeypatch):
        import src.core.ratelimit.storage.valkey as valkey_module
        from src.core.ratelimit.storage import ValkeyStorage

        monkeypatch.setattr(valkey_module, "get_client", lambda: None)
        monkeypatch.setattr(valkey_module, "is_available", lambda: False)
        storage = ValkeyStorage()

        assert await storage.aget_bucket("b") is None
        assert await storage.adelete_bucket("b") is False
        assert await storage.aget_keys_by_prefix("p:") == []
        assert await storage.aincrement("c", "f", 1) == 0
        allowed, _, _ = await storage.aeval_token_bucket(
            "tb", capacity=1, refill_rate=1.0, cost=1
        )
        assert allowed is False


def fake_items(state):
    """Pull the list field out of a stored bucket (convenience for tests)."""
    return state["items"]


class TestModuleAsyncApi:
    """The module-level async surface mirrors the blocking one."""

    @pytest.mark.asyncio
    async def test_acheck_requires_setup(self, rl_module_state):
        rl = rl_module_state
        rl._manager = None
        rl._setup_complete = False

        with pytest.raises(RuntimeError):
            await rl.acheck_rate_limit(user_id=1, route="GET /x")

    @pytest.mark.asyncio
    async def test_acheck_and_reset_roundtrip(self, rl_module_state):
        rl = rl_module_state
        rl._manager = None
        rl._setup_complete = False

        assert rl.setup(storage_backend=MemoryStorage(), enable_global_limit=False)

        result = await rl.acheck_rate_limit(user_id=4242, route="GET /x")
        assert result.allowed is True

        assert await rl.aincrement_custom_usage("k", "count", 1, ttl=60) == 1
        assert await rl.aget_bucket_info("k") is not None

        await rl.areset_all()
        assert await rl.aincrement_custom_usage("k", "count", 0, ttl=60) == 0


class _StubResult:
    def __init__(self, allowed=True, retry_after=None):
        self.allowed = allowed
        self.retry_after = retry_after


async def _drive_middleware(middleware, path="/api/v1/users/@me"):
    """Run one HTTP request through an ASGI middleware, collecting messages."""
    sent = []
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("203.0.113.5", 12345),
        "state": {},
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    await middleware(app)(scope, receive, send)
    return sent


class TestEventLoopCallersUseAsyncPath:
    """The middleware and decorators must await, not block."""

    @pytest.mark.asyncio
    async def test_middleware_awaits_async_check(self, monkeypatch):
        import src.core.ratelimit as rl
        from src.core.ratelimit.middleware import RateLimitMiddlewareASGI

        awaited = []

        async def _fake_acheck(**_kwargs):
            awaited.append(True)
            return _StubResult(allowed=True)

        monkeypatch.setattr(rl, "is_setup", lambda: True)
        monkeypatch.setattr(rl, "acheck_rate_limit", _fake_acheck)
        monkeypatch.setattr(rl, "get_headers", lambda _result: {})

        sent = await _drive_middleware(RateLimitMiddlewareASGI)

        assert awaited == [True]
        assert sent[0]["status"] == 200

    @pytest.mark.asyncio
    async def test_middleware_returns_429_when_denied(self, monkeypatch):
        import src.core.ratelimit as rl
        from src.core.ratelimit.middleware import RateLimitMiddlewareASGI

        class _Denied(_StubResult):
            pass

        async def _fake_acheck(**_kwargs):
            denied = _Denied(allowed=False, retry_after=5)
            denied.response_body = {"error": "limited"}
            return denied

        monkeypatch.setattr(rl, "is_setup", lambda: True)
        monkeypatch.setattr(rl, "acheck_rate_limit", _fake_acheck)
        monkeypatch.setattr(rl, "get_headers", lambda _result: {"Retry-After": "5"})

        sent = await _drive_middleware(RateLimitMiddlewareASGI)

        assert sent[0]["status"] == 429

    @pytest.mark.asyncio
    async def test_decorator_awaits_async_check(self, rl_module_state, monkeypatch):
        rl = rl_module_state
        rl._manager = None
        rl._setup_complete = False
        assert rl.setup(storage_backend=MemoryStorage(), enable_global_limit=False)

        awaited = []

        async def _fake_acheck(**_kwargs):
            awaited.append(True)
            return _StubResult(allowed=True)

        monkeypatch.setattr(rl, "acheck_rate_limit", _fake_acheck)
        monkeypatch.setattr(rl, "get_headers", lambda _result: {})

        from src.core.ratelimit.decorators import rate_limit

        @rate_limit(requests=5, window_seconds=60)
        async def handler(request: Request):
            return "ok"

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/decorated",
                "headers": [],
                "query_string": b"",
                "scheme": "http",
                "server": ("testserver", 80),
                "client": ("203.0.113.9", 1234),
                "state": {},
            }
        )

        assert await handler(request) == "ok"
        assert awaited == [True]


# ---------------------------------------------------------------------------
# Atomicity under parallel requests
# ---------------------------------------------------------------------------
# The limiter exists to admit at most N requests per window, so the failure
# mode worth testing is not a wrong flag in one call -- it is two overlapping
# calls both reading the same bucket snapshot and both being admitted. A test
# built on MemoryStorage alone would pass even if a composite operation were
# split across awaits, because its ``a*`` methods call straight through with
# no suspension point. These two storages make the window real:
#
# * ``_YieldingStorage`` awaits before every backend call, like a network
#   round trip, so the gap between a read and its write is genuinely open to
#   other coroutines;
# * ``_ThreadOffloadStorage`` keeps RateLimitStorage's default
#   ``asyncio.to_thread`` offload, which is what any third-party backend
#   inherits and which must carry composite operations as ONE call.


class _YieldingStorage(MemoryStorage):
    """MemoryStorage with an await point before each backend call."""

    async def aget_bucket(self, key: str) -> Optional[Dict[str, Any]]:
        await asyncio.sleep(0)
        return self.get_bucket(key)

    async def aincrement(self, key: str, field: str, amount: int = 1) -> int:
        await asyncio.sleep(0)
        return self.increment(key, field, amount)

    async def aupdate_bucket(
        self,
        key: str,
        mutate_fn: Callable[[Dict[str, Any]], None],
        ttl: Optional[float] = None,
    ) -> None:
        await asyncio.sleep(0)
        self.update_bucket(key, mutate_fn, ttl)

    async def aeval_token_bucket(
        self, key: str, capacity: int, refill_rate: float, cost: int, ttl: int = 86400
    ) -> tuple:
        await asyncio.sleep(0)
        return self.eval_token_bucket(key, capacity, refill_rate, cost, ttl)


class _ThreadOffloadStorage(MemoryStorage):
    """MemoryStorage keeping the base ``asyncio.to_thread`` async defaults."""

    aget_bucket = RateLimitStorage.aget_bucket
    aincrement = RateLimitStorage.aincrement
    aupdate_bucket = RateLimitStorage.aupdate_bucket
    aeval_token_bucket = RateLimitStorage.aeval_token_bucket


async def _race(manager: RateLimitManager, count: int, **kwargs) -> list:
    """Fire ``count`` checks at once against the same bucket."""
    return await asyncio.gather(
        *(manager.acheck_rate_limit(**kwargs) for _ in range(count))
    )


class TestAtomicityUnderParallelRequests:
    """Concurrency must never buy a caller extra requests."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "storage_factory", [_YieldingStorage, _ThreadOffloadStorage]
    )
    async def test_token_bucket_admits_exactly_the_limit(self, storage_factory):
        manager = _manager(limit=5, storage=storage_factory())

        results = await _race(manager, 40, user_id=1, route="GET /x")

        allowed = [r for r in results if r.allowed]
        assert len(allowed) == 5
        assert len(results) == 40
        # Each admitted request consumed a distinct token: two callers that
        # read the same snapshot would have reported the same ``remaining``.
        assert sorted(r.remaining for r in allowed) == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "algorithm",
        [
            RateLimitAlgorithm.FIXED_WINDOW,
            RateLimitAlgorithm.SLIDING_WINDOW,
            RateLimitAlgorithm.LEAKY_BUCKET,
        ],
    )
    async def test_legacy_read_modify_write_is_not_split(self, algorithm):
        """Racing callers admit exactly as many as serial ones.

        The legacy algorithms do get_bucket -> mutate -> set_bucket through
        ``aupdate_bucket``. If the async path ever split that composite into
        separate awaits, the parallel run would admit strictly more.
        """
        serial_manager = _manager(limit=5, algorithm=algorithm)
        serial = [
            serial_manager.check_rate_limit(user_id=3, route="GET /x")
            for _ in range(40)
        ]

        parallel_manager = _manager(
            limit=5, algorithm=algorithm, storage=_YieldingStorage()
        )
        parallel = await _race(parallel_manager, 40, user_id=3, route="GET /x")

        admitted_serial = sum(r.allowed for r in serial)
        admitted_parallel = sum(r.allowed for r in parallel)
        assert admitted_parallel == admitted_serial
        # The limiter actually engaged, so the comparison is not vacuous.
        assert 0 < admitted_serial < 40

    @pytest.mark.asyncio
    async def test_middleware_admits_exactly_the_limit_end_to_end(
        self, rl_module_state
    ):
        """The real ASGI path, 20 requests in flight at once."""
        rl = rl_module_state
        rl._manager = None
        rl._setup_complete = False
        assert rl.setup(
            storage_backend=_YieldingStorage(),
            route_configs={
                "GET /users/@me": RateLimitConfig(
                    requests=3,
                    window_seconds=60.0,
                    burst=0,
                    algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
                    scope=BucketType.ROUTE,
                )
            },
            enable_global_limit=False,
        )

        from src.core.ratelimit.middleware import RateLimitMiddlewareASGI

        sent_batches = await asyncio.gather(
            *(
                _drive_middleware(RateLimitMiddlewareASGI, path="/api/v1/users/@me")
                for _ in range(20)
            )
        )
        statuses = [batch[0]["status"] for batch in sent_batches]

        assert statuses.count(200) == 3
        assert statuses.count(429) == 17


class _LockingFakeValkey:
    """Fake GLIDE client that really locks and yields on every round trip.

    ``aacquire_lock`` spins until the holder releases, mirroring the real
    client's blocking acquire. Combined with the yields in ``aget_json`` /
    ``aset_json`` this exposes any window between a read and its write.
    """

    def __init__(self):
        self.data: Dict[str, Dict[str, Any]] = {}
        self.held: set = set()
        self.inside = 0
        self.max_inside = 0
        self.acquire_failures = 0

    async def aacquire_lock(self, key, timeout=5.0):
        while key in self.held:
            await asyncio.sleep(0)
        self.held.add(key)
        return "token"

    async def arelease_lock(self, key, token):
        self.held.discard(key)
        return True

    async def aget_json(self, key):
        await asyncio.sleep(0)
        self.inside += 1
        self.max_inside = max(self.max_inside, self.inside)
        return self.data.get(key)

    async def aset_json(self, key, value, ttl=None):
        await asyncio.sleep(0)
        self.data[key] = dict(value)
        self.inside -= 1
        return True


class _DeadlockFakeValkey(_LockingFakeValkey):
    """Fake whose lock is always held by somebody else."""

    async def aacquire_lock(self, key, timeout=5.0):
        self.acquire_failures += 1
        return None


class TestValkeyStorageConcurrency:
    """The distributed backend's read-modify-write must be serialised."""

    @pytest.fixture
    def locking_valkey(self, monkeypatch):
        import src.core.ratelimit.storage.valkey as valkey_module
        from src.core.ratelimit.storage import ValkeyStorage

        fake = _LockingFakeValkey()
        monkeypatch.setattr(valkey_module, "get_client", lambda: fake)
        monkeypatch.setattr(valkey_module, "is_available", lambda: True)
        return ValkeyStorage(), fake

    @pytest.mark.asyncio
    async def test_concurrent_increments_lose_no_update(self, locking_valkey):
        storage, fake = locking_valkey

        results = await asyncio.gather(
            *(storage.aincrement("ctr", "count", 1) for _ in range(25))
        )

        # One writer at a time: nobody was inside the read-modify-write
        # while somebody else was.
        assert fake.max_inside == 1
        # Every increment observed the previous write, so no update was lost
        # and no two callers computed from the same snapshot.
        assert sorted(results) == list(range(1, 26))
        assert [state["count"] for state in fake.data.values()] == [25]
        # The lock is not leaked.
        assert fake.held == set()

    @pytest.mark.asyncio
    async def test_increment_fails_closed_when_lock_is_taken(self, monkeypatch):
        import src.core.ratelimit.storage.valkey as valkey_module
        from src.core.ratelimit.storage import ValkeyStorage

        fake = _DeadlockFakeValkey()
        monkeypatch.setattr(valkey_module, "get_client", lambda: fake)
        monkeypatch.setattr(valkey_module, "is_available", lambda: True)
        storage = ValkeyStorage()

        assert await storage.aincrement("ctr", "count", 1) == 0
        # Fail closed, and never touch the bucket without the lock.
        assert fake.data == {}
        assert fake.acquire_failures == 1
