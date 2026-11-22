"""Regression: concurrent cold ``has_perm:*`` misses collapse to one resolution.

``RolePermissionMixin._compute_has_permission`` is the
``@cached(ttl=10, prefix="has_perm")`` verdict tier behind ``has_permission``.
Role/permission mutations call ``invalidate_pattern("has_perm:*")``, which drops
that single cached verdict.  The boolean verdict is asked for from many request
threads at once (message-list hydration, poll hydration, event-audience fan-out,
notification fan-out), so on the resulting cold miss every thread re-entered the
expensive ``get_permissions`` resolver -- one DB-backed permission-snapshot
query per in-flight check.  A single role change could therefore stampede the
DB with N SELECTs (a thundering-herd availability spike; the verdict itself is
unchanged).

The verdict cache must coalesce concurrent cold misses into a single
authoritative resolution, keyed by the (user, server, permission, channel) cache
key, so N simultaneous checks cost one DB read.  Availability only -- no
authorization decision changes.
"""

import threading
import time

import pytest

from src.core.database import invalidate_pattern
from src.core.database.cache import decorators
from src.core.database.cache.decorators import reset_flight_state_internal
from src.core.servers.channel_access_cache import get_channel_access_cache  # noqa: F401
from src.core.servers.handlers.roles.permissions import RolePermissionMixin


class _Stub(RolePermissionMixin):
    """Minimal host for the mixin; only the cached method's deps are supplied."""

    def __init__(self, resolver):
        self._resolver = resolver

    def get_permissions(self, user_id, server_id, channel_id=None):
        return self._resolver(user_id, server_id, channel_id)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # Force the process-local tier so the test is independent of any shared
    # Valkey state: every caller must see the same cold key.
    monkeypatch.setattr(decorators, "get_client", lambda: None)
    monkeypatch.setattr(decorators, "is_available", lambda: False)
    invalidate_pattern("has_perm:*")
    get_channel_access_cache().clear()
    reset_flight_state_internal()
    yield
    invalidate_pattern("has_perm:*")
    get_channel_access_cache().clear()
    reset_flight_state_internal()


def _run_concurrent(stub, n_callers, channel_id):
    per_call_delay = 0.02
    calls = []
    loader_lock = threading.Lock()

    # The patched get_permissions IS the DB-backed resolver the verdict tier
    # wraps.  Serializing it makes the stampede measurable in wall-clock time
    # (N * delay without coalescing, ~1 * delay with it).
    def resolver(user_id, server_id, cid):
        calls.append((user_id, server_id, cid))
        with loader_lock:
            time.sleep(per_call_delay)
        return {"administrator": False, "messages.send": True}

    stub._resolver = resolver  # noqa: SLF001 - intentional test seam

    barrier = threading.Barrier(n_callers)
    results = []
    errors = []

    def worker():
        try:
            barrier.wait()
            results.append(
                stub.has_permission(42, 7, "messages.send", channel_id=channel_id)
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

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
    # Before coalescing this is one resolver call per cold miss (N), which is
    # the measurable DB stampede the single-flight gate removes.
    assert len(calls) == 1, f"expected 1 DB-backed verdict, got {len(calls)}"
    assert elapsed < n_callers * per_call_delay * 0.5, (
        f"cold misses were not coalesced ({elapsed:.3f}s for {n_callers} callers)"
    )
    return calls


def test_concurrent_cold_has_perm_misses_coalesce_channel_scoped():
    """N channel-scoped cold misses run the resolver exactly once."""
    calls = _run_concurrent(_Stub(lambda *a: {}), n_callers=16, channel_id=99)
    assert len(calls) == 1, f"expected 1 DB-backed verdict, got {len(calls)}"
    assert calls[0][:2] == (42, 7)


def test_concurrent_cold_has_perm_misses_coalesce_server_scoped():
    """N server-scoped (no channel) cold misses run the resolver exactly once."""
    calls = _run_concurrent(_Stub(lambda *a: {}), n_callers=16, channel_id=None)
    assert len(calls) == 1, f"expected 1 DB-backed verdict, got {len(calls)}"
    assert calls[0][:2] == (42, 7)
