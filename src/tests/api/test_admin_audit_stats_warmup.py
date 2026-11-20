"""Regression: the admin audit-stats summary is warmed at boot, off-request.

``get_or_compute("audit_stats", (today_start,), ...)`` is the first thing the
frequently-polled ``GET /admin/audit/logs/stats`` does after its host and
permission gates. On a cold process the producer runs a conditional-aggregate
scan over ``admin_audit_log`` while a request is held open. The summary is request-invariant
(a system-wide payload with no per-admin data), so the boot warm-up primes that
one entry on the shared config-gated worker thread and the first real poll is a
cache hit.

These tests pin the wiring end-to-end: the warm-up primes the exact entry the
route reads, the first request skips the cold work, both auth gates still run
before the cache is read (and a denial is never served from a warm entry), and
the cold/warm cost is measured. Warming is best-effort and non-fatal.
"""

import asyncio
import threading
import time
from datetime import datetime as real_datetime, timezone
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api import warmup
from src.api.routes.admin import _summary_cache
from src.api.routes.admin import audit as route

_STATS_URL = "/api/v1/admin/audit/logs/stats"
_FIXED_NOW = real_datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


class _FixedDateTime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return _FIXED_NOW.replace(tzinfo=None)
        return _FIXED_NOW.astimezone(tz)


class CountingDB:
    """Fake DB: counts the aggregate scans, with optional CPU work per scan."""

    def __init__(self, count=5, work=0):
        self.fetch_one_calls = 0
        self.count = count
        self.work = work

    def fetch_one(self, query, params=None):
        self.fetch_one_calls += 1
        if "total_today" in query:
            return {name: self.count for name in route._AUDIT_STATS_COLUMNS}
        if self.work:
            # Deterministic stand-in for a slow COUNT scan (no wall-clock sleep).
            sum(i * i for i in range(self.work))
        return {"count": self.count}


@pytest.fixture(autouse=True)
def _cold_audit_stats():
    _summary_cache.clear("audit_stats")
    yield
    _summary_cache.clear("audit_stats")


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1/admin")
    return TestClient(app)


def test_warmup_primes_cache_and_first_request_skips_cold_work():
    db = CountingDB()
    with (
        patch("src.api.get_db", return_value=db),
        patch.object(route, "datetime", _FixedDateTime),
        patch.object(route, "check_host_restriction"),
        patch.object(route, "require_admin_permission"),
    ):
        # Off-request warm-up pays the full cold build.
        route.warm_audit_stats_cache()
        assert db.fetch_one_calls == 1  # one conditional-aggregate query

        first = _client().get(_STATS_URL)

    assert first.status_code == 200
    assert first.json()["total_today"] == 5
    assert db.fetch_one_calls == 1, (
        "the first request must hit the warmed entry, not rebuild the aggregate"
    )


def test_gates_still_run_before_the_warm_cache_read():
    db = CountingDB()
    with (
        patch("src.api.get_db", return_value=db),
        patch.object(route, "datetime", _FixedDateTime),
    ):
        route.warm_audit_stats_cache()
        with (
            patch.object(route, "check_host_restriction") as host_gate,
            patch.object(route, "require_admin_permission") as perm_gate,
        ):
            client = _client()
            first = client.get(_STATS_URL)
            second = client.get(_STATS_URL)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    # Both gates run on every request, including the two cache hits.
    assert host_gate.call_count == 2
    assert perm_gate.call_count == 2
    assert perm_gate.call_args_list[0].args[1] == "admin.audit.read"
    assert db.fetch_one_calls == 1  # neither hit recomputed the summary


def test_warm_entry_never_serves_a_denied_request():
    db = CountingDB()
    with (
        patch("src.api.get_db", return_value=db),
        patch.object(route, "datetime", _FixedDateTime),
    ):
        route.warm_audit_stats_cache()
        with (
            patch.object(route, "check_host_restriction"),
            patch.object(
                route,
                "require_admin_permission",
                side_effect=HTTPException(status_code=403, detail="denied"),
            ),
        ):
            resp = _client().get(_STATS_URL)

    assert resp.status_code == 403


def test_cold_build_cost_vs_warmed_read():
    # Deterministic stand-in for one slow aggregate scan. With batching the
    # cold build issues a single query, so scale the work to keep the cold/warm
    # timing separation well above scheduler noise.
    work_per_scan = 500_000
    day = route._audit_stats_day_start()

    cold_db = CountingDB(work=work_per_scan)
    with (
        patch("src.api.get_db", return_value=cold_db),
        patch.object(route, "datetime", _FixedDateTime),
    ):
        t0 = time.perf_counter()
        cold = route.get_or_compute(
            "audit_stats", (day,), lambda: route._build_audit_stats(day), 2.0
        )
        cold_ms = (time.perf_counter() - t0) * 1000
    cold_calls = cold_db.fetch_one_calls

    _summary_cache.clear("audit_stats")
    warm_db = CountingDB(work=work_per_scan)
    with (
        patch("src.api.get_db", return_value=warm_db),
        patch.object(route, "datetime", _FixedDateTime),
    ):
        # The cold cost is paid here, off the request path.
        route.warm_audit_stats_cache()
        t0 = time.perf_counter()
        warm = route.get_or_compute(
            "audit_stats",
            (route._audit_stats_day_start(),),
            lambda: route._build_audit_stats(route._audit_stats_day_start()),
            2.0,
        )
        warm_ms = (time.perf_counter() - t0) * 1000

    print(
        f"\n  cold build: {cold_ms:.2f} ms ({cold_calls} queries) "
        f"| warm read: {warm_ms:.3f} ms"
    )
    assert cold == warm
    assert cold_calls == 1
    assert warm_db.fetch_one_calls == 1  # the warm-up did all the querying
    assert warm_ms < cold_ms / 3


def test_warmup_is_non_fatal_without_a_database():
    with (
        patch("src.api.get_db", return_value=None),
        patch.object(route, "datetime", _FixedDateTime),
    ):
        # _build_audit_stats raises when the DB is unavailable; warm() swallows it.
        route.warm_audit_stats_cache()


def test_warm_once_invokes_audit_stats_warmup(monkeypatch):
    called = []
    monkeypatch.setattr(warmup, "_warm_audit_stats", lambda: called.append(True))
    assert warmup.warm_once() >= 0.0
    assert called == [True]


def test_warm_audit_stats_delegates_to_the_route(monkeypatch):
    called = []
    monkeypatch.setattr(route, "warm_audit_stats_cache", lambda: called.append(True))
    warmup._warm_audit_stats()
    assert called == [True]


def test_warmup_loop_runs_off_the_event_loop_thread(monkeypatch):
    seen = {}

    def record(app=None):
        seen["thread"] = threading.current_thread()

    monkeypatch.setattr(warmup, "_warmup_enabled", lambda: True)
    monkeypatch.setattr(warmup, "_warmup_delay", lambda: 0.0)
    monkeypatch.setattr(warmup, "warm_once", record)
    asyncio.run(warmup.warmup_loop())

    assert seen["thread"] is not threading.main_thread()
