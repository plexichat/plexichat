"""Regression: rate-limit principals must canonicalize equivalent IP spellings.

The auth failure-budget subsystem canonicalizes source IPs (see
``src.core.auth.managers.session.login._canonical_source_ip``) because a client
behind a trusted proxy controls the textual spelling of the address carried in
``X-Forwarded-For``. The rate limiter did not: ``normalise_ip_address`` returned
the raw string, so ``2001:db8::1``, ``2001:0db8::1`` and the IPv4-mapped
``::ffff:a.b.c.d`` form each landed in a different bucket. One source could
rotate spellings to get a fresh budget on every request and grow the keyspace
without bound.

Canonicalize parseable addresses before they reach key generation. Unparseable
values keep the existing bounded/shared-bucket behaviour (no check is weakened).
"""

import pytest

from src.core.ratelimit.manager.constants import MAX_COMPONENT_KEY_LENGTH
from src.core.ratelimit.manager.core import RateLimitManager
from src.core.ratelimit.manager.principals import (
    INVALID_IP_PRINCIPAL,
    normalise_ip_address,
)
from src.core.ratelimit.models import RateLimitAlgorithm, RateLimitConfig

pytestmark = pytest.mark.ratelimit

# One IPv6 address, four textual spellings. All are accepted by
# ``ipaddress.ip_address`` (and therefore by the trusted-proxy XFF walk in
# ``src.utils.net.get_client_ip``) but were four distinct bucket keys.
_SPELLINGS = (
    "2001:db8::1",
    "2001:0db8::1",
    "2001:db8:0:0:0:0:0:1",
    "2001:0db8:0:0:0:0:0:1",
)


def _manager() -> RateLimitManager:
    cfg = RateLimitConfig(
        requests=2,
        window_seconds=60,
        burst=0,
        algorithm=RateLimitAlgorithm.FIXED_WINDOW,
    )
    return RateLimitManager(
        route_configs={"GET /t": cfg},
        ip_config=cfg,
        enable_global_limit=False,
    )


def test_ipv6_spelling_rotation_shares_one_bucket():
    # Every spelling reduces to the same canonical principal.
    assert {normalise_ip_address(ip) for ip in _SPELLINGS} == {"2001:db8::1"}

    manager = _manager()
    results = [
        manager.check_rate_limit(ip_address=ip, route="GET /t") for ip in _SPELLINGS
    ]

    # One source, one budget: the third request from the same address must be
    # denied no matter which spelling it presents.
    assert [r.allowed for r in results] == [True, True, False, False]
    # Every denial comes from the one shared per-IP bucket.
    denied = [r for r in results if not r.allowed]
    assert len({r.bucket_key for r in denied}) == 1
    assert "2001%3Adb8%3A%3A1" in denied[0].bucket_key


def test_ipv4_mapped_form_shares_bucket_with_plain_ipv4():
    assert normalise_ip_address("198.51.100.7") == "198.51.100.7"
    assert normalise_ip_address("::ffff:198.51.100.7") == "198.51.100.7"

    manager = _manager()
    results = [
        manager.check_rate_limit(ip_address=ip, route="GET /t")
        for ip in ("198.51.100.7", "::ffff:198.51.100.7", "198.51.100.7")
    ]

    assert [r.allowed for r in results] == [True, True, False]
    assert "2001" not in results[-1].bucket_key
    assert "198.51.100.7" in results[-1].bucket_key


def test_trusted_proxy_xff_spelling_rotation_cannot_multiply_budget(monkeypatch):
    """End-to-end: spellings arriving via a trusted proxy's XFF share a bucket."""
    import utils.config as config

    from src.utils.net import get_client_ip

    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: (
            {"trusted_proxies": ["10.0.0.0/8"]} if key == "api" else (default or {})
        ),
    )

    def client_ip(spelling: str):
        return get_client_ip(
            {
                "client": ("10.0.0.1", 4000),
                "headers": [(b"x-forwarded-for", spelling.encode())],
            }
        )

    # The extractor honours XFF from the trusted peer and yields the raw
    # spelling; principal normalization is what must collapse the variants.
    principals = {normalise_ip_address(client_ip(s)) for s in _SPELLINGS[:3]}
    assert principals == {"2001:db8::1"}

    manager = _manager()
    results = []
    for spelling in _SPELLINGS[:3]:
        ip = client_ip(spelling)
        assert ip is not None
        results.append(manager.check_rate_limit(ip_address=ip, route="GET /t"))

    assert [r.allowed for r in results] == [True, True, False]


def test_existing_bounds_and_control_char_rejection_are_preserved():
    assert normalise_ip_address("x" * (MAX_COMPONENT_KEY_LENGTH + 1)) == (
        INVALID_IP_PRINCIPAL
    )
    assert normalise_ip_address("bad\x00ip") == INVALID_IP_PRINCIPAL
    assert normalise_ip_address("bad\x7fip") == INVALID_IP_PRINCIPAL
    assert normalise_ip_address(None) is None
    assert normalise_ip_address("") is None
