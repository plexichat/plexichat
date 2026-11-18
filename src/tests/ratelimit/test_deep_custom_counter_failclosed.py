"""Regression: custom usage counters must fail closed on a silent backend.

``aincrement_custom_usage`` reports the authoritative counter back to
abuse-limit callers (sticker creation, friend requests, upload-byte budgets).
A backend that cannot run the mutation must never let the caller fall back to
reporting the requested ``amount`` instead -- that turns a store outage into
an unlimited path.
"""

import pytest

import src.core.ratelimit.storage.valkey as valkey_storage
from src.core.ratelimit.exceptions import RateLimitStoreUnavailable
from src.core.ratelimit.manager import RateLimitManager
from src.core.ratelimit.storage import MemoryStorage, ValkeyStorage


class _SilentStorage(MemoryStorage):
    """Backend that returns without ever running ``mutate_fn``."""

    def update_bucket(self, key, mutate_fn, ttl=None):
        return None

    async def aupdate_bucket(self, key, mutate_fn, ttl=None):
        return None


class TestValkeyAupdateBucketOutage:
    @pytest.mark.asyncio
    async def test_unavailable_client_raises_instead_of_returning(self, monkeypatch):
        monkeypatch.setattr(valkey_storage, "get_client", lambda: None)
        storage = ValkeyStorage()
        called = []

        def mutate(state):
            called.append(True)

        with pytest.raises(RateLimitStoreUnavailable):
            await storage.aupdate_bucket("k", mutate, ttl=60)
        assert called == []

    @pytest.mark.asyncio
    async def test_unavailable_flag_raises_instead_of_returning(self, monkeypatch):
        monkeypatch.setattr(
            valkey_storage, "get_client", lambda: object()
        )
        monkeypatch.setattr(valkey_storage, "is_available", lambda: False)
        storage = ValkeyStorage()
        with pytest.raises(RateLimitStoreUnavailable):
            await storage.aupdate_bucket("k", lambda state: state.update(v=1), ttl=60)


class TestManagerCustomCounterFailsClosed:
    def test_sync_silent_backend_does_not_report_requested_amount(self):
        manager = RateLimitManager(storage_backend=_SilentStorage())
        with pytest.raises(RateLimitStoreUnavailable):
            manager.increment_custom_usage("c:1", "count", 1, ttl=60)

    @pytest.mark.asyncio
    async def test_async_silent_backend_does_not_report_requested_amount(self):
        manager = RateLimitManager(storage_backend=_SilentStorage())
        with pytest.raises(RateLimitStoreUnavailable):
            await manager.aincrement_custom_usage("c:1", "count", 1, ttl=60)

    def test_healthy_backend_still_returns_authoritative_value(self):
        manager = RateLimitManager(storage_backend=MemoryStorage())
        assert manager.increment_custom_usage("c:1", "count", 1, ttl=60) == 1
        assert manager.increment_custom_usage("c:1", "count", 2, ttl=60) == 3
