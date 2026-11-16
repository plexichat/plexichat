"""Regression: distributed-limit consumption must be serialized by a lock.

A read-modify-write performed without holding the backend lock lets two
concurrent workers spend the same token (the later write clobbers the earlier
deduction). These tests pin the rule that an unacquirable lock means "no
mutation" -- never a best-effort unlocked write.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
from typing import Any, Dict, List, Optional

import pytest

from src.core.ratelimit.exceptions import RateLimitStoreUnavailable
from src.core.ratelimit.manager import RateLimitManager
from src.core.ratelimit.models import RateLimitAlgorithm, RateLimitConfig
from src.core.ratelimit.storage import MemoryStorage, RateLimitStorage
from src.core.ratelimit.storage import valkey as valkey_module
from src.core.ratelimit.storage.valkey import ValkeyStorage


class LockedStore(RateLimitStorage):
    """Minimal distributed-style backend that uses the base ``update_bucket``."""

    def __init__(self) -> None:
        self._memory = MemoryStorage()
        self._lock = threading.Lock()
        self.deny_lock = False

    def get_bucket(self, key: str) -> Optional[Dict[str, Any]]:
        return self._memory.get_bucket(key)

    def set_bucket(self, key: str, state: Dict[str, Any], ttl=None) -> None:
        self._memory.set_bucket(key, state, ttl)

    def delete_bucket(self, key: str) -> bool:
        return self._memory.delete_bucket(key)

    def get_keys_by_prefix(self, prefix: str) -> List[str]:
        return self._memory.get_keys_by_prefix(prefix)

    def delete_by_prefix(self, prefix: str) -> int:
        return self._memory.delete_by_prefix(prefix)

    def clear_all(self) -> None:
        self._memory.clear_all()

    def increment(self, key: str, field: str, amount: int = 1) -> int:
        return self._memory.increment(key, field, amount)

    def get_and_set(self, key, field, value, default=None):
        return self._memory.get_and_set(key, field, value, default)

    def add_to_list(self, key, field, value, max_size=1000) -> int:
        return self._memory.add_to_list(key, field, value, max_size)

    def trim_list(self, key, field, min_value) -> int:
        return self._memory.trim_list(key, field, min_value)

    def acquire_lock(self, key: str, timeout: float = 1.0) -> Optional[str]:
        if self.deny_lock:
            return None
        return "token" if self._lock.acquire(timeout=timeout) else None

    def release_lock(self, key: str, token: Optional[str] = None) -> None:
        try:
            self._lock.release()
        except RuntimeError:
            pass

    def eval_token_bucket(self, key, capacity, refill_rate, cost, ttl=86400) -> tuple:
        return self._memory.eval_token_bucket(key, capacity, refill_rate, cost, ttl)


def _manager(storage: RateLimitStorage, requests: int = 5) -> RateLimitManager:
    return RateLimitManager(
        storage_backend=storage,
        route_configs={
            "GET /t": RateLimitConfig(
                requests=requests,
                window_seconds=60.0,
                burst=0,
                algorithm=RateLimitAlgorithm.FIXED_WINDOW,
            )
        },
        enable_global_limit=False,
    )


class TestDistributedLockSafety:
    def test_unlocked_rmw_is_refused_and_state_is_untouched(self):
        store = LockedStore()
        store.deny_lock = True

        with pytest.raises(RateLimitStoreUnavailable, match="acquire rate-limit lock"):
            store.update_bucket("bucket", lambda state: state.update(count=1))

        assert store.get_bucket("bucket") is None

    def test_locked_rmw_still_applies_updates(self):
        store = LockedStore()

        store.update_bucket("bucket", lambda state: state.update(count=1))

        assert store.get_bucket("bucket") == {"count": 1}

    def test_lock_acquisition_error_is_not_swallowed(self):
        store = LockedStore()

        def broken_lock(*args: Any, **kwargs: Any) -> None:
            raise ConnectionError("lock service down")

        store.acquire_lock = broken_lock  # type: ignore[method-assign]
        with pytest.raises(RateLimitStoreUnavailable):
            store.update_bucket("bucket", lambda state: state.update(count=1))

    def test_concurrent_burst_cannot_double_spend(self):
        store = LockedStore()
        manager = _manager(store, requests=5)
        barrier = threading.Barrier(24)

        def attempt(_: int) -> bool:
            barrier.wait(timeout=10)
            return manager.check_rate_limit(user_id=7, route="GET /t").allowed

        with ThreadPoolExecutor(max_workers=24) as pool:
            outcomes = list(pool.map(attempt, range(24)))

        assert sum(outcomes) == 5
        state = store.get_bucket("route:u:7:r:GET /t")
        assert state is not None and state["request_count"] == 5

    @pytest.mark.asyncio
    async def test_valkey_aupdate_bucket_refuses_without_lock(self, monkeypatch):
        storage = ValkeyStorage()

        class _TouchedClient:
            async def aget_json(self, *args: Any, **kwargs: Any) -> Any:
                raise AssertionError("bucket must not be read without a lock")

        monkeypatch.setattr(valkey_module, "get_client", lambda: _TouchedClient())
        monkeypatch.setattr(valkey_module, "is_available", lambda: True)

        async def no_lock(*args: Any, **kwargs: Any) -> None:
            return None

        monkeypatch.setattr(storage, "aacquire_lock", no_lock)

        with pytest.raises(
            RateLimitStoreUnavailable, match="distributed rate-limit lock"
        ):
            await storage.aupdate_bucket("bucket", lambda state: state.update(count=1))

    @pytest.mark.asyncio
    async def test_async_manager_uses_locked_path(self):
        store = LockedStore()
        manager = _manager(store, requests=3)

        results = await asyncio.gather(
            *[manager.acheck_rate_limit(user_id=9, route="GET /t") for _ in range(12)]
        )

        assert sum(bool(result.allowed) for result in results) == 3
