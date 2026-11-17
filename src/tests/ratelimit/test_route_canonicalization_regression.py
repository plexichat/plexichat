"""Regression tests: raw URL paths must not fan out rate-limit buckets.

Callers do not always pass a canonical ``"METHOD /route"`` pattern to the
manager. The authentication middleware passes ``f"{method} {request.url.path}"``
verbatim, and the ``@rate_limit`` decorator uses ``request.url.path`` (which
contains concrete path parameters). Without canonicalization inside the
manager those strings become distinct bucket keys, so a client can evade a
strict per-route cap simply by varying a path segment.

The manager must normalize any ``"METHOD /path"`` string the same way the ASGI
middleware does before it looks up a config or builds a storage key.
"""

import pytest

from src.core.ratelimit.manager import RateLimitManager
from src.core.ratelimit.storage import MemoryStorage


@pytest.fixture
def manager():
    return RateLimitManager(storage_backend=MemoryStorage())


def test_raw_prefixed_path_with_id_shares_one_bucket(manager):
    """A varying concrete id in a raw path cannot open a fresh bucket."""
    keys = set()
    results = []
    for i in range(12):
        # This is the shape the auth middleware's IP fallback passes for a
        # resource route; the concrete id used to mint a per-request bucket.
        result = manager.check_rate_limit(
            ip_address="9.9.9.9",
            route=f"POST /api/v1/channels/{i}/messages",
        )
        keys.add(result.bucket_key)
        results.append(result.allowed)

    assert len(keys) == 1, f"raw paths fanned out into {len(keys)} buckets"


def test_raw_path_uses_the_strict_route_config(manager):
    """A raw prefixed path must resolve to its strict route config, not the default."""
    first = manager.check_rate_limit(
        ip_address="1.2.3.4", route="POST /api/v1/auth/login"
    )
    assert first.headers.limit == 5, (
        f"raw path used the generic default limit ({first.headers.limit}), "
        "not the strict login cap"
    )


def test_decorator_route_with_concrete_id_shares_bucket(manager):
    """Concrete ids in a decorator route must collapse to the canonical pattern."""
    keys = set()
    for i in range(10):
        result = manager.check_rate_limit(
            user_id=42,
            route=f"decorator:POST /api/v1/channels/{i}/messages",
        )
        keys.add(result.bucket_key)
    assert len(keys) == 1, f"decorator route ids fanned out into {len(keys)} buckets"
