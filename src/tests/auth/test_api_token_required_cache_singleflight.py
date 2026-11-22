"""Regression: cold API-access-token-required misses collapse to one DB read.

``ApiTokenMixin.is_api_access_token_required`` memoises the authn-gate verdict
behind ``@cached(ttl=30, prefix="access_token_required")``. Every API access
token create/revoke/update calls ``invalidate_pattern("access_token_required*")``,
which drops that single verdict. The verdict is then recomputed on the next
request -- and the auth middleware runs this sync method in a threadpool for
*every* non-public API request, so on the resulting cold miss all concurrent
requests run the expensive ``auth_api_access_tokens`` SELECT themselves. A
single token mutation can therefore stampede the DB with one SELECT per
in-flight request (a thundering-herd availability spike; the verdict itself is
unchanged).

The cache must coalesce those concurrent cold misses into a single DB read,
keyed by the (argument-free) cache key, so N simultaneous checks cost one
SELECT. Availability only -- this changes no authorization decision.
"""

import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.core.auth.managers.api_token import ApiTokenMixin
from src.core.database import invalidate_pattern
from src.core.database.cache import decorators
from src.core.database.cache.decorators import reset_flight_state_internal


class _Stub(ApiTokenMixin):
    """Minimal host for the mixin; only the cached method's deps are supplied."""

    def __init__(self, db):
        self._db = db
        self._config = {}

    def _get_timestamp(self):
        return 4102444800


@pytest.fixture(autouse=True)
def _isolate():
    invalidate_pattern("access_token_required*")
    reset_flight_state_internal()
    yield
    invalidate_pattern("access_token_required*")
    reset_flight_state_internal()


def test_concurrent_cold_access_token_required_misses_coalesce(monkeypatch):
    """N concurrent cold misses run the expensive DB SELECT exactly once.

    Before the fix this is N SELECTs (and ~N*delay latency), which is the
    measurable stampede the single-flight gate removes.
    """
    # Force the process-local cache tier so the test is independent of any
    # shared Valkey state: every caller sees the same cold key.
    monkeypatch.setattr(decorators, "get_client", lambda: None)
    monkeypatch.setattr(decorators, "is_available", lambda: False)

    n_callers = 16
    per_call_delay = 0.02
    calls = []
    loader_lock = threading.Lock()

    class _SlowDB:
        def fetch_one(self, sql, params):
            calls.append(1)
            with loader_lock:  # serialize the cold loader -> N*delay without SF
                time.sleep(per_call_delay)
            return {"id": 1}

    stub = _Stub(_SlowDB())
    # Force the config branch so the loader reaches the DB SELECT.
    cfg = SimpleNamespace(access_token_required=True)

    barrier = threading.Barrier(n_callers)
    results = []
    errors = []

    def worker():
        try:
            barrier.wait()
            results.append(stub.is_api_access_token_required())
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    with patch("src.api.config.get_api_config", return_value=cfg):
        threads = [threading.Thread(target=worker) for _ in range(n_callers)]
        start = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.perf_counter() - start

    assert errors == []
    assert len(results) == n_callers
    assert all(r is True for r in results)
    assert len(calls) == 1, f"expected 1 DB-backed verdict, got {len(calls)}"
    # Without coalescing this is ~n_callers * per_call_delay.
    assert elapsed < n_callers * per_call_delay * 0.5
