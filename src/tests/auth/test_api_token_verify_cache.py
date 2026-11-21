"""Regression: API access-token introspection is bounded, binding-aware cached.

``verify_api_access_token`` is the per-request introspection performed by the
auth middleware. Each call re-derived the blind index, ran a SELECT for the
token row, decrypted/constant-time-compared the secret and ran a second SELECT
for its scopes. None of that depends on the request path/method, so repeating
it for the same credential was pure waste.

The cache memoises only that read-only introspection (a small verdict dict);
usage/audit writes stay outside it. These tests pin the work reduction and
prove the cache cannot be used to bypass revocation, scope/IP binding, expiry,
or exact-secret checks -- and that no raw credential reaches a cache key.
"""

import time

import pytest

from src.core.auth._lazy import _get_auth_manager
from src.core.auth.managers.api_token import (
    _API_TOKEN_VERIFY_CACHE,
    _API_TOKEN_VERIFY_MAX_ENTRIES,
    _invalidate_api_token_verify_cache,
)
from src.core.bounded_lru import BoundedLRU

pytestmark = [pytest.mark.auth]


@pytest.fixture(autouse=True)
def _clear_verify_cache():
    """Isolate the process-local cache between tests."""
    _invalidate_api_token_verify_cache()
    yield
    _invalidate_api_token_verify_cache()


def _mgr(auth_manager):
    return _get_auth_manager().get_instance()


def _count_introspections(monkeypatch, mgr):
    """Wrap the read-only introspection so we can count DB-backed misses."""
    real = mgr._introspect_api_access_token
    calls = {"n": 0}

    def spy(token, ip_address):
        calls["n"] += 1
        return real(token, ip_address)

    monkeypatch.setattr(mgr, "_introspect_api_access_token", spy)
    return calls


_IP = "203.0.113.10"
_UA = "regression-agent/1.0"


def test_repeated_verification_does_one_introspection(auth_manager, monkeypatch):
    """N verifications of one credential cost one introspection, not N."""
    mgr = _mgr(auth_manager)
    token = mgr.create_api_access_token("cached", created_by=1)
    calls = _count_introspections(monkeypatch, mgr)

    n = 50
    for _ in range(n):
        assert (
            mgr.verify_api_access_token(
                token.token,
                ip_address=_IP,
                user_agent=_UA,
                path="/api/v1/users/@me",
                method="GET",
            )
            is True
        )

    # Before caching this was 50 blind-index + decrypt + 2 SELECT sequences.
    assert calls["n"] == 1
    assert isinstance(_API_TOKEN_VERIFY_CACHE, BoundedLRU)


def test_before_after_introspection_reduction(auth_manager, monkeypatch, capsys):
    """Measure the reduction: uncached baseline vs bounded cache, same calls."""
    mgr = _mgr(auth_manager)
    token = mgr.create_api_access_token("bench", created_by=1)
    kw = dict(ip_address=_IP, user_agent=_UA, path="/api/v1/messages", method="POST")
    n = 40

    # Baseline: clear the cache before every call so each one introspects.
    for _ in range(n):
        _invalidate_api_token_verify_cache()
        assert mgr.verify_api_access_token(token.token, **kw) is True
    before = n  # one introspection per call with no cache

    # Optimised: identical call pattern, cache allowed to work.
    _invalidate_api_token_verify_cache()
    calls = _count_introspections(monkeypatch, mgr)
    for _ in range(n):
        assert mgr.verify_api_access_token(token.token, **kw) is True
    after = calls["n"]

    with capsys.disabled():
        print(
            f"\nintrospections for {n} identical verifications: "
            f"before(no cache)={before}, after(cache)={after}, "
            f"reduction={100.0 * (before - after) / before:.0f}%"
        )
    assert before == n
    assert after == 1
    assert after < before


def test_binding_context_is_part_of_cache_key(auth_manager, monkeypatch):
    """A verdict resolved for one IP/UA is never reused for another."""
    mgr = _mgr(auth_manager)
    token = mgr.create_api_access_token("bound", created_by=1)
    calls = _count_introspections(monkeypatch, mgr)

    assert mgr.verify_api_access_token(token.token, ip_address=_IP, user_agent=_UA)
    assert mgr.verify_api_access_token(token.token, ip_address=_IP, user_agent=_UA)
    assert calls["n"] == 1  # repeated in the same context hits the cache

    # Different IP and different UA are distinct keys -> re-introspect.
    assert mgr.verify_api_access_token(
        token.token, ip_address="203.0.113.11", user_agent=_UA
    )
    assert calls["n"] == 2
    assert mgr.verify_api_access_token(
        token.token, ip_address=_IP, user_agent="other/1"
    )
    assert calls["n"] == 3


def test_ip_scope_enforcement_survives_cache_hit(auth_manager):
    """An IP-scoped token denied in one context is re-evaluated per IP."""
    mgr = _mgr(auth_manager)
    token = mgr.create_api_access_token("scoped", created_by=1, scope_mode="enforce")
    mgr.add_api_access_token_scope(token.id, "cidr", "198.51.100.0/24", 1)

    # Allowed IP: warm a positive verdict.
    assert (
        mgr.verify_api_access_token(
            token.token, ip_address="198.51.100.5", user_agent=_UA
        )
        is True
    )
    # A different IP must not ride the cached positive verdict.
    assert (
        mgr.verify_api_access_token(
            token.token, ip_address="203.0.113.8", user_agent=_UA
        )
        is False
    )
    # Original IP still works (the denial was keyed to its own context).
    assert (
        mgr.verify_api_access_token(
            token.token, ip_address="198.51.100.5", user_agent=_UA
        )
        is True
    )


def test_revocation_drops_cached_verdict(auth_manager):
    """A revoked token cannot authenticate from a warm positive verdict."""
    mgr = _mgr(auth_manager)
    token = mgr.create_api_access_token("revoked", created_by=1)

    assert (
        mgr.verify_api_access_token(token.token, ip_address=_IP, user_agent=_UA) is True
    )

    assert mgr.revoke_api_access_token(token.id, revoked_by=1) is True

    # Without invalidation this would still return the cached True.
    assert (
        mgr.verify_api_access_token(token.token, ip_address=_IP, user_agent=_UA)
        is False
    )


def test_expiry_rechecked_on_cache_hit(auth_manager, monkeypatch):
    """A token that expires inside the TTL is denied without a re-introspect."""
    mgr = _mgr(auth_manager)
    token = mgr.create_api_access_token(
        "expiring", created_by=1, expires_at=int(time.time() * 1000) + 150
    )
    calls = _count_introspections(monkeypatch, mgr)

    assert (
        mgr.verify_api_access_token(token.token, ip_address=_IP, user_agent=_UA) is True
    )
    time.sleep(0.25)

    # Cache TTL is 10s, but the cached expiry is already past: denied on the hit.
    assert (
        mgr.verify_api_access_token(token.token, ip_address=_IP, user_agent=_UA)
        is False
    )
    assert calls["n"] == 1  # the denial came from the cache-hit re-check


def test_exact_secret_still_required(auth_manager):
    """Blind-index variants that fail the exact compare are still rejected."""
    mgr = _mgr(auth_manager)
    token = mgr.create_api_access_token("exact", created_by=1)
    raw = token.token

    assert mgr.verify_api_access_token(raw, ip_address=_IP, user_agent=_UA) is True
    assert (
        mgr.verify_api_access_token(raw.lower(), ip_address=_IP, user_agent=_UA)
        is False
    )
    assert (
        mgr.verify_api_access_token(raw.upper(), ip_address=_IP, user_agent=_UA)
        is False
    )
    assert (
        mgr.verify_api_access_token(f"  {raw}  ", ip_address=_IP, user_agent=_UA)
        is False
    )
    assert mgr.verify_api_access_token(raw, ip_address=_IP, user_agent=_UA) is True


def test_cache_is_bounded(auth_manager):
    """Distinct credentials cannot grow the cache past its hard bounds."""
    mgr = _mgr(auth_manager)
    for i in range(_API_TOKEN_VERIFY_MAX_ENTRIES + 50):
        # Arbitrary (never-issued) credentials: each introspects and caches a
        # negative verdict under a unique digest.
        mgr.verify_api_access_token(f"never-issued-{i}", ip_address=_IP, user_agent=_UA)

    stats = _API_TOKEN_VERIFY_CACHE.stats()
    assert stats["size"] <= _API_TOKEN_VERIFY_MAX_ENTRIES
    assert stats["size"] <= stats["max_entries"]
    assert stats["bytes"] <= stats["max_bytes"]
    assert stats["evictions"] > 0


def test_no_raw_token_in_cache_keys(auth_manager):
    """Only SHA-256 digests reach the keyspace, never the credential itself."""
    mgr = _mgr(auth_manager)
    token = mgr.create_api_access_token("secret-keeper", created_by=1)
    raw = token.token
    assert raw and len(raw) >= 8

    mgr.verify_api_access_token(raw, ip_address=_IP, user_agent=_UA)
    with _API_TOKEN_VERIFY_CACHE._lock:
        keys = list(_API_TOKEN_VERIFY_CACHE._data.keys())

    assert keys, "expected at least one cached verdict"
    for key in keys:
        token_digest, binding_digest = key
        assert raw not in token_digest and raw not in binding_digest
        assert _IP not in binding_digest and _UA not in binding_digest
        assert len(token_digest) == 64 and len(binding_digest) == 64
