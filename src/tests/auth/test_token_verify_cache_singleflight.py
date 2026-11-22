"""Regression: cold token-verification misses collapse to one DB-backed check.

``VerificationMixin._verify_token`` memoises the expensive session/bot lookup
behind ``@cached(ttl=10, prefix="token_verify")``. Revocation, privilege change
and logout all call ``invalidate_pattern("token_verify:*")``, which drops every
verdict. On the resulting cold miss every concurrent request used to run the
full session SELECT + constant-time hash comparison itself: a single
invalidation could stampede the DB with one expensive verification per in-flight
request (a thundering-herd availability spike, not an authz bypass).

The cache must coalesce those concurrent cold misses into a single computation,
keyed by the credential-partitioned cache key, so N simultaneous verifications
of the same token cost one DB-backed check.
"""

import threading
import time
from unittest.mock import patch

import pytest

from src.core.auth.managers.session.verification import VerificationMixin
from src.core.auth.models import AccountType, TokenInfo
from src.core.database import invalidate_pattern
from src.core.database.cache import decorators
from src.core.database.cache.decorators import reset_flight_state_internal

# Structurally valid but never echoed; only its digest reaches a cache key.
_TOKEN = "123456789." + "a" * 43
_PARSED = {"token_type": "session", "id": 123456789, "secret": "a" * 43}
_IP = "203.0.113.10"
_UA = "regression-agent/1.0"


def _info():
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=7,
        user_id=7,
        session_id="s-1",
        username="u",
        permissions={},
        account_type=AccountType.USER,
        rate_limit_tier="standard",
        expires_at=4102444800000,
    )


class _Stub(VerificationMixin):
    def __init__(self):
        self._config = {"security": {}, "sessions": {}}
        self._db = object()


@pytest.fixture(autouse=True)
def _isolate():
    invalidate_pattern("token_verify:*")
    reset_flight_state_internal()
    yield
    invalidate_pattern("token_verify:*")
    reset_flight_state_internal()


def test_concurrent_cold_token_verifications_coalesce_to_one_db_check(monkeypatch):
    """N concurrent cold misses run the expensive verifier exactly once.

    Before the fix this is N verifications (and ~N*n*delay latency), which is
    the measurable stampede the single-flight gate removes.
    """
    # Force the process-local cache tier so the test is independent of any
    # shared Valkey state: every caller sees the same cold key.
    monkeypatch.setattr(decorators, "get_client", lambda: None)
    monkeypatch.setattr(decorators, "is_available", lambda: False)

    stub = _Stub()
    calls = []
    loader_lock = threading.Lock()
    n_callers = 16
    per_call_delay = 0.02

    def slow_verify(*a, **k):
        calls.append(1)
        with loader_lock:  # serialize the cold loader -> N*delay without SF
            time.sleep(per_call_delay)
        return _info()

    barrier = threading.Barrier(n_callers)
    results = []
    errors = []

    def worker():
        try:
            barrier.wait()
            results.append(stub.verify_token(_TOKEN, _IP, _UA))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    with (
        patch(
            "src.core.auth.managers.session.verification.parse_token",
            return_value=_PARSED,
        ),
        patch.object(
            VerificationMixin, "_verify_session_token", side_effect=slow_verify
        ),
    ):
        threads = [threading.Thread(target=worker) for _ in range(n_callers)]
        start = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.perf_counter() - start

    assert errors == []
    assert len(results) == n_callers
    assert all(r.user_id == 7 for r in results)
    assert len(calls) == 1, f"expected 1 DB-backed verify, got {len(calls)}"
    # Without coalescing this is ~n_callers * per_call_delay.
    assert elapsed < n_callers * per_call_delay * 0.5
