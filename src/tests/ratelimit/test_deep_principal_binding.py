"""Regression: per-principal rate limits must not be client-definable.

An attacker who can choose the identity components that feed a bucket key can
either forge another principal's key or grow the keyspace without bound by
rotating the value. These tests pin the normalization boundary introduced in
``manager.principals`` for both the sync and async check paths.
"""

from typing import Any

import pytest

from src.core.ratelimit.manager import RateLimitManager
from src.core.ratelimit.manager.constants import MAX_TOTAL_KEY_LENGTH
from src.core.ratelimit.models import BucketType, RateLimitAlgorithm, RateLimitConfig
from src.core.ratelimit.storage import MemoryStorage


def _config(requests: int = 2) -> RateLimitConfig:
    return RateLimitConfig(
        requests=requests,
        window_seconds=60.0,
        burst=0,
        algorithm=RateLimitAlgorithm.FIXED_WINDOW,
    )


def _manager(storage: MemoryStorage) -> RateLimitManager:
    return RateLimitManager(
        storage_backend=storage,
        route_configs={"GET /t": _config()},
        ip_config=_config(),
        enable_global_limit=False,
    )


class TestPrincipalBinding:
    def test_forged_user_id_cannot_inject_key_segments(self, memory_storage):
        manager = _manager(memory_storage)

        # A raw string id containing ':' used to be interpolated verbatim,
        # creating a key that looked like it contained a 'u:2' segment.
        result = manager.check_rate_limit(
            user_id="1:u:2", ip_address="10.0.0.1", route="GET /t"
        )
        assert ":u:" not in result.bucket_key
        assert "u:1%3Au:2" not in result.bucket_key

        # Even the low-level key builder escapes forged id strings, so a
        # direct caller cannot smuggle segments through either.
        forged = manager._generate_bucket_key(
            BucketType.ROUTE, user_id="1:u:2", route="GET /t"
        )
        assert "u:1%3Au%3A2" in forged
        assert len(forged) <= MAX_TOTAL_KEY_LENGTH

    def test_digit_string_and_int_are_the_same_principal(self, memory_storage):
        manager = _manager(memory_storage)

        as_string = manager.check_rate_limit(user_id="007", route="GET /t")
        as_int = manager.check_rate_limit(user_id=7, route="GET /t")

        assert as_string.bucket_key == as_int.bucket_key

    @pytest.mark.parametrize("bad_id", [True, -1, 1.5, 1 << 200, "", " 12x ", object()])
    def test_malformed_ids_are_not_distinct_buckets(self, memory_storage, bad_id: Any):
        manager = _manager(memory_storage)

        result = manager.check_rate_limit(
            user_id=bad_id, ip_address="10.0.0.1", route="GET /t"
        )

        # Invalid ids fall back to the IP principal, never to a bucket keyed
        # on the attacker-controlled value itself.
        assert "u:" not in result.bucket_key
        assert ":ip:10.0.0.1" in result.bucket_key

    def test_authenticated_principal_survives_ip_rotation(self, memory_storage):
        manager = _manager(memory_storage)

        first = manager.check_rate_limit(
            user_id=7, ip_address="10.0.0.1", route="GET /t"
        )
        rotated = manager.check_rate_limit(
            user_id=7, ip_address="10.0.0.2", route="GET /t"
        )

        assert first.bucket_key == rotated.bucket_key
        # No IP-derived bucket may exist once a trusted principal is known.
        assert memory_storage.get_keys_by_prefix("ip:") == []
        assert first.allowed and rotated.allowed
        assert not manager.check_rate_limit(
            user_id=7, ip_address="10.0.0.3", route="GET /t"
        ).allowed

    def test_rotating_garbage_ips_share_one_bounded_bucket(self, memory_storage):
        manager = _manager(memory_storage)
        seen = set()

        for index in range(500):
            result = manager.check_rate_limit(
                ip_address=f"attacker-{index}-" + "x" * 200,
                route="GET /t",
            )
            seen.add(result.bucket_key)

        assert seen <= {"route:ip:invalid-ip:r:GET /t", "ip:ip:invalid-ip"}
        assert memory_storage.get_stats()["bucket_count"] <= 2

    @pytest.mark.asyncio
    async def test_async_path_normalizes_identities_too(self, memory_storage):
        manager = _manager(memory_storage)

        forged = await manager.acheck_rate_limit(
            user_id="1:u:2", ip_address="10.0.0.1", route="GET /t"
        )
        normal = await manager.acheck_rate_limit(
            user_id=42, ip_address="10.0.0.1", route="GET /t"
        )

        assert ":u:" not in forged.bucket_key
        assert "u:42" in normal.bucket_key
