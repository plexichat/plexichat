"""Regression: resource-scoped limits must keep the unauthenticated principal.

``generate_bucket_key`` falls back to ``ip_address`` when no ``user_id`` is
present. The resource-scoped check used to drop ``ip_address`` entirely, so
every unauthenticated client hit ONE shared ``resource:r:<route>:res:<id>``
bucket. Two clients on different addresses therefore shared (and could
exhaust) each other's budget, violating the documented "per IP when
unauthenticated" invariant.

Authenticated behaviour is unchanged: ``generate_bucket_key`` prefers a
canonical ``user_id`` over the IP, so rotating the IP must not mint a new
bucket for a logged-in user.
"""

from typing import Any, cast

import pytest

from src.core.ratelimit.manager import RateLimitManager
from src.core.ratelimit.models import (
    BucketType,
    RateLimitAlgorithm,
    RateLimitConfig,
)
from src.core.ratelimit.storage import MemoryStorage

ROUTE = "POST /channels/{id}/messages"
RESOURCE_ID = 4242


def _config() -> RateLimitConfig:
    return RateLimitConfig(
        requests=2,
        window_seconds=60.0,
        algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        scope=BucketType.RESOURCE,
    )


def _manager(storage: MemoryStorage) -> RateLimitManager:
    # No global/IP limit: the resource bucket is the one under test.
    return RateLimitManager(
        storage_backend=storage,
        route_configs={ROUTE: _config()},
        enable_global_limit=False,
    )


class TestUnauthenticatedResourceScope:
    def test_distinct_ips_get_distinct_resource_buckets(self, memory_storage):
        manager = _manager(memory_storage)

        first = manager.check_rate_limit(
            ip_address="198.51.100.7", route=ROUTE, resource_id=RESOURCE_ID
        )
        second = manager.check_rate_limit(
            ip_address="198.51.100.8", route=ROUTE, resource_id=RESOURCE_ID
        )

        assert first.bucket_type is BucketType.RESOURCE
        assert first.bucket_key != second.bucket_key
        assert ":ip:198.51.100.7" in first.bucket_key
        assert ":ip:198.51.100.8" in second.bucket_key

    def test_one_ip_cannot_exhaust_another_ips_budget(self, memory_storage):
        manager = _manager(memory_storage)

        # The attacker consumes both slots of their own bucket.
        assert manager.check_rate_limit(
            ip_address="203.0.113.1", route=ROUTE, resource_id=RESOURCE_ID
        ).allowed
        assert manager.check_rate_limit(
            ip_address="203.0.113.1", route=ROUTE, resource_id=RESOURCE_ID
        ).allowed
        assert not manager.check_rate_limit(
            ip_address="203.0.113.1", route=ROUTE, resource_id=RESOURCE_ID
        ).allowed

        # A different, unauthenticated client still has its full budget.
        assert manager.check_rate_limit(
            ip_address="203.0.113.2", route=ROUTE, resource_id=RESOURCE_ID
        ).allowed
        assert manager.check_rate_limit(
            ip_address="203.0.113.2", route=ROUTE, resource_id=RESOURCE_ID
        ).allowed

    @pytest.mark.asyncio
    async def test_async_path_scopes_unauthenticated_by_ip(self, memory_storage):
        manager = _manager(memory_storage)

        first = await manager.acheck_rate_limit(
            ip_address="192.0.2.10", route=ROUTE, resource_id=RESOURCE_ID
        )
        second = await manager.acheck_rate_limit(
            ip_address="192.0.2.11", route=ROUTE, resource_id=RESOURCE_ID
        )

        assert first.bucket_type is BucketType.RESOURCE
        assert first.bucket_key != second.bucket_key
        assert ":ip:192.0.2.10" in first.bucket_key
        assert ":ip:192.0.2.11" in second.bucket_key

    def test_authenticated_user_stays_scoped_by_user_not_ip(self, memory_storage):
        manager = _manager(memory_storage)

        first = manager.check_rate_limit(
            user_id=cast(Any, 77),
            ip_address="10.0.0.1",
            route=ROUTE,
            resource_id=RESOURCE_ID,
        )
        rotated = manager.check_rate_limit(
            user_id=cast(Any, 77),
            ip_address="10.0.0.2",
            route=ROUTE,
            resource_id=RESOURCE_ID,
        )

        assert first.bucket_key == rotated.bucket_key
        assert ":u:77" in first.bucket_key
        assert "ip:" not in first.bucket_key
