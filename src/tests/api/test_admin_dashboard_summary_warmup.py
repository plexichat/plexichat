"""Regression: the admin dashboard summary is warmed at boot, off-request.

``get_or_compute("dashboard", (), _build_dashboard_response)`` is the first
thing the frequently-polled ``GET /admin/dashboard`` does after the host and
permission gates. On a cold process the producer assembles the whole response
(ticket counts, telemetry endpoint stats, dashboard counts, system metrics,
feature stats and worker health) on the request path. The router lifespan now
primes that single, request-invariant entry on a worker thread so the first
real poll is a cache hit.

These tests pin the warm-up wiring, the cold/warm cost, its idempotency and
best-effort failure handling -- and, critically, that both auth gates still run
on every request. The cache only holds the shared system-wide summary, never
an authorization decision or per-admin data.
"""

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.api.routes.admin import _summary_cache
from src.api.routes.admin import dashboard as route


@pytest.fixture(autouse=True)
def _cold_dashboard_summary():
    _summary_cache.clear("dashboard")
    yield
    _summary_cache.clear("dashboard")


def _counting_loader(delay=0.0):
    """A deterministic stand-in for the cold dashboard build with a known cost."""
    calls = []
    value = {"total_users": 7, "db_status": "healthy"}

    def loader():
        calls.append(1)
        if delay:
            time.sleep(delay)
        return value

    return calls, loader


def test_warm_primes_dashboard_summary(monkeypatch):
    calls, loader = _counting_loader()
    monkeypatch.setattr(route, "_build_dashboard_response", loader)

    route.warm_admin_dashboard_summary_cache()
    assert calls == [1]

    # A subsequent read is a hit; the expensive builder does not rerun.
    result = route.get_or_compute("dashboard", (), loader, 2.0)
    assert result == {"total_users": 7, "db_status": "healthy"}
    assert calls == [1]


def test_cold_first_hit_costs_the_build_and_warm_hit_skips_it(monkeypatch):
    cold_cost = 0.03  # stand-in for the ticket/telemetry/metrics queries
    calls, loader = _counting_loader(delay=cold_cost)
    monkeypatch.setattr(route, "_build_dashboard_response", loader)

    # Cold path: the first read pays the full build.
    t0 = time.perf_counter()
    route.get_or_compute("dashboard", (), loader, 2.0)
    cold_elapsed = time.perf_counter() - t0

    # Warm the cache off-request, then time a read that must be a hit.
    _summary_cache.clear("dashboard")
    route.warm_admin_dashboard_summary_cache()
    t0 = time.perf_counter()
    route.get_or_compute("dashboard", (), loader, 2.0)
    warm_elapsed = time.perf_counter() - t0

    assert cold_elapsed >= cold_cost
    assert warm_elapsed < cold_cost / 3
    assert warm_elapsed < cold_elapsed / 3
    assert len(calls) == 2  # one cold build + one warm-up build, no third build


def test_dashboard_summary_warmup_is_idempotent(monkeypatch):
    calls, loader = _counting_loader()
    monkeypatch.setattr(route, "_build_dashboard_response", loader)

    route.warm_admin_dashboard_summary_cache()
    route.warm_admin_dashboard_summary_cache()

    assert calls == [1]


def test_dashboard_summary_warmup_is_non_fatal(monkeypatch):
    def boom():
        raise RuntimeError("dashboard build exploded")

    monkeypatch.setattr(route, "_build_dashboard_response", boom)

    # A broken build must be logged and swallowed, never raised into startup.
    route.warm_admin_dashboard_summary_cache()


def test_router_lifespan_primes_dashboard_summary(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    calls = []
    monkeypatch.setattr(
        route, "warm_admin_dashboard_summary_cache", lambda: calls.append(True)
    )

    app = FastAPI()
    app.include_router(route.router)
    with TestClient(app):
        pass

    assert calls == [True]


def test_lifespan_runs_warmup_off_the_event_loop_thread(monkeypatch):
    seen = {}

    def record():
        seen["thread"] = threading.current_thread()

    monkeypatch.setattr(route, "warm_admin_dashboard_summary_cache", record)

    async def drive():
        async with route._dashboard_summary_lifespan(None):
            pass

    asyncio.run(drive())

    assert "thread" in seen
    assert seen["thread"] is not threading.main_thread()


def test_first_request_uses_warmed_summary_but_both_gates_still_run(monkeypatch):
    calls, loader = _counting_loader()
    monkeypatch.setattr(route, "_build_dashboard_response", loader)

    # Prime through the boot warm-up path, not through a request.
    route.warm_admin_dashboard_summary_cache()
    assert calls == [1]

    request = MagicMock()
    with (
        patch.object(route, "check_host_restriction") as host_gate,
        patch.object(route, "require_admin_permission") as perm_gate,
    ):
        first = asyncio.run(route.get_dashboard(request))
        second = asyncio.run(route.get_dashboard(request))

    # Both requests hit the warmed entry; no cold re-evaluation.
    assert first == {"total_users": 7, "db_status": "healthy"}
    assert second == first
    assert calls == [1]
    # ...but both auth gates ran on each request exactly as before.
    assert host_gate.call_count == 2
    assert perm_gate.call_count == 2
    assert perm_gate.call_args_list[0].args[1] == "metrics.read"
