"""Concurrent identical admin-summary reads must coalesce into one compute.

``get_or_compute`` backs the most-polled admin endpoints (dashboard, reports,
system alerts).  Each producer runs a dozen-plus COUNT/full-scan queries, so a
burst of identical polls on a cold cache (or just after TTL expiry) used to run
that whole batch once *per concurrent caller* -- a thundering herd that scales
with the number of pollers and workers.

These tests pin the single-flight behaviour:

* N concurrent callers for the same key run the producer exactly once and all
  receive the same value (the "before" behaviour ran it N times);
* callers with different keys (different authz/DB scope) are never coalesced,
  so one scope's computed summary can never be handed to another;
* a producer failure is shared with waiters and is never cached;
* in-flight bookkeeping is bounded and fully released after the burst.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import src.api as api
from src.api.routes.admin import _summary_cache


# A stable DB double so _make_key() produces the same key on every thread.
_DB = SimpleNamespace(config={"path": "/tmp/singleflight-test.db"})

# ``api.get_db`` is patched ONCE, in the test thread, around the whole burst.
# Patching it inside every worker thread looks equivalent but is not:
# ``mock.patch`` saves/restores the module attribute globally, so overlapping
# enters and exits restore each other's stub and the module keeps pointing at a
# previous thread's double after the burst. That leaked a SimpleNamespace
# "database" into every later test in the same worker (auth middleware then
# 500s on db.close()/db.type). A thread-local lookup keeps per-scope keys while
# the patch itself stays single-owner.
_THREAD_DB = threading.local()


def _current_db():
    return getattr(_THREAD_DB, "db", _DB)


def _use_db(db):
    _THREAD_DB.db = db


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _stats() -> dict:
    return _summary_cache.stats()


def test_concurrent_identical_reads_compute_once():
    """8 concurrent identical polls share one producer run and one value."""
    _summary_cache.clear()
    _summary_cache.reset_stats()

    entered = threading.Event()
    release = threading.Event()
    calls = {"n": 0}

    def producer():
        calls["n"] += 1
        entered.set()
        assert release.wait(3.0), "leader producer was never released"
        return {"total": 7, "items": [1, 2, 3]}

    def run(results):
        results.append(_summary_cache.get_or_compute("dashboard", (), producer))

    results: list = []
    threads = []

    with patch.object(api, "get_db", _current_db):
        leader = threading.Thread(target=run, args=(results,))
        leader.start()
        assert entered.wait(3.0), "leader never entered the producer"

        # While the leader is blocked, launch the rest. They must join its
        # flight rather than start their own producers.
        for _ in range(7):
            t = threading.Thread(target=run, args=(results,))
            t.start()
            threads.append(t)

        assert _wait_until(lambda: _stats()["coalesced"] >= 7), (
            "waiters did not coalesce onto the leader's in-flight compute"
        )
        release.set()
        for t in [leader, *threads]:
            t.join(timeout=3.0)
            assert not t.is_alive(), "a caller failed to finish"

    assert calls["n"] == 1, "producer ran more than once for identical callers"
    assert results == [{"total": 7, "items": [1, 2, 3]}] * 8
    assert _stats()["computes"] == 1
    assert _stats()["inflight"] == 0, "in-flight state was not released"


def test_different_db_scopes_are_not_coalesced():
    """Two concurrent callers with different DB scopes never share a value.

    This is the cross-user/cross-tenant guard: the single-flight key is the
    same authz-scoped key the cache itself uses, so distinct scopes compute
    independently even when they overlap in time.
    """
    _summary_cache.clear()
    _summary_cache.reset_stats()

    db_a = SimpleNamespace(config={"path": "/tmp/scope-a.db"})
    db_b = SimpleNamespace(config={"path": "/tmp/scope-b.db"})
    entered = {"a": threading.Event(), "b": threading.Event()}
    release = threading.Event()
    values = {"a": {"scope": "a"}, "b": {"scope": "b"}}

    def producer(scope):
        entered[scope].set()
        assert release.wait(3.0), f"producer {scope} was never released"
        return values[scope]

    results: dict = {}

    def run(scope):
        _use_db(db_a if scope == "a" else db_b)
        results[scope] = _summary_cache.get_or_compute(
            "alerts", (), lambda: producer(scope)
        )

    with patch.object(api, "get_db", _current_db):
        ta = threading.Thread(target=run, args=("a",))
        tb = threading.Thread(target=run, args=("b",))
        ta.start()
        tb.start()
        assert entered["a"].wait(3.0) and entered["b"].wait(3.0), (
            "both scopes must run their own producer concurrently"
        )

        release.set()
        ta.join(timeout=3.0)
        tb.join(timeout=3.0)
        assert not ta.is_alive() and not tb.is_alive()

    assert results == values
    assert _stats()["computes"] == 2
    assert _stats()["coalesced"] == 0


def test_producer_error_is_shared_and_never_cached():
    """A failed compute is propagated to waiters and leaves no cached value."""
    _summary_cache.clear()
    _summary_cache.reset_stats()

    entered = threading.Event()
    release = threading.Event()
    boom = RuntimeError("db unavailable")

    def producer():
        entered.set()
        assert release.wait(3.0)
        raise boom

    outcomes: list = []

    def run():
        try:
            outcomes.append(
                ("ok", _summary_cache.get_or_compute("reports", (), producer))
            )
        except RuntimeError as exc:
            outcomes.append(("err", exc))

    with patch.object(api, "get_db", _current_db):
        leader = threading.Thread(target=run)
        leader.start()
        assert entered.wait(3.0)

        waiters = [threading.Thread(target=run) for _ in range(3)]
        for t in waiters:
            t.start()
        assert _wait_until(lambda: _stats()["coalesced"] >= 3)

        release.set()
        for t in [leader, *waiters]:
            t.join(timeout=3.0)
            assert not t.is_alive()

    assert len(outcomes) == 4
    assert all(kind == "err" for kind, _ in outcomes)
    assert all(exc is boom for _, exc in outcomes)
    assert _stats()["inflight"] == 0
    # A later call retries the producer rather than serving a failed/empty value.
    _summary_cache.reset_stats()
    with patch.object(api, "get_db", _current_db):
        assert _summary_cache.get_or_compute("reports", (), lambda: "recovered") == (
            "recovered"
        )
    assert _stats()["computes"] == 1
