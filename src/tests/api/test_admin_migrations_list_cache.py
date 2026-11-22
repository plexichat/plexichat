"""Regression tests for the bounded response cache on GET /admin/migrations.

The status assembly is expensive on a cold process (schema creation, history
queries, checksum/AST parsing of every migration file). The list handler keeps
the fully assembled response in a short-TTL, DB-scoped cache that is primed at
startup, so the first real request and subsequent admin-dashboard polls do not
re-pay that cost. These tests cover both correctness (identical response shape
and content) and the performance-relevant behaviour (cache reuse, bounded size,
DB scoping) while proving the security gates still run on every request.
"""

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from src.api.routes.admin import migrations as route


@pytest.fixture(autouse=True)
def _clean_list_cache():
    route._clear_migration_list_cache()
    yield
    route._clear_migration_list_cache()


def _manager(db_path, *, applied=3, pending=0):
    """A manager-like object whose DB resolves to a real sqlite path.

    Only real DB shapes are cached: the key builder returns None for mocked DB
    objects, so tests must present a sqlite-shaped DB to exercise the cache.
    """
    manager = MagicMock()
    manager.db = MagicMock()
    manager.db.type = "sqlite"
    manager.db.config = {"type": "sqlite", "path": str(db_path)}

    records = [
        {"version": f"{i:03d}", "name": f"migration-{i}", "status": "completed"}
        for i in range(applied)
    ]
    pending_versions = [f"{applied + i:03d}" for i in range(pending)]
    manager.get_migration_status.return_value = {
        "applied_migrations": [r["version"] for r in records],
        "pending_migrations": pending_versions,
        "failed_migrations": [],
        "all_records": records,
    }
    manager.tracker.get_migration_metadata.side_effect = lambda v: {}
    manager.tracker.can_run_irreversible_migration.return_value = (True, "ok")
    return manager


def _response(manager):
    request = MagicMock()
    # The route now gates through ``require_admin_permission`` (the action
    # scope plus ``maintenance``), so patch that gate rather than the token
    # helper it used to call directly; the host gate is the assertion target.
    with (
        patch.object(route, "check_host_restriction") as host_gate,
        patch.object(route, "require_admin_permission", return_value=1),
    ):
        result = asyncio.run(
            route.list_migrations(request, manager=manager, admin_user=1)
        )
    return result, host_gate


def test_cached_response_is_identical_and_skips_rebuild(tmp_path, monkeypatch):
    manager = _manager(tmp_path / "m.db", applied=4, pending=2)

    build_calls = []
    real_build = route._build_migration_list

    def slow_build(mgr):
        build_calls.append(1)
        time.sleep(0.02)  # stand-in for the real cold DB/file cost
        return real_build(mgr)

    monkeypatch.setattr(route, "_build_migration_list", slow_build)

    t0 = time.perf_counter()
    first, _ = _response(manager)
    cold_elapsed = time.perf_counter() - t0

    t0 = time.perf_counter()
    second, _ = _response(manager)
    warm_elapsed = time.perf_counter() - t0

    # Correctness: same shape and content, including pending-only entries.
    assert [m.version for m in first.migrations] == [
        "000",
        "001",
        "002",
        "003",
        "004",
        "005",
    ]
    assert second.model_dump() == first.model_dump()
    assert (second.applied_count, second.pending_count, second.failed_count) == (
        4,
        2,
        0,
    )

    # Performance: the expensive builder ran once; the cached poll skipped it.
    assert len(build_calls) == 1
    assert manager.get_migration_status.call_count == 1

    # The cached poll must also be materially cheaper. Each side keeps its best
    # run: the point is that 20ms of work is skipped, so one preemption on a
    # loaded CI worker must not decide the comparison.
    def _best(fn, reps=5):
        best = float("inf")
        for _ in range(reps):
            start = time.perf_counter()
            fn()
            best = min(best, time.perf_counter() - start)
        return best

    def _cold():
        route._clear_migration_list_cache()
        _response(manager)

    cold_elapsed = _best(_cold)
    warm_elapsed = _best(lambda: _response(manager))
    assert warm_elapsed < cold_elapsed / 2


def test_security_gate_runs_on_every_request_including_cache_hits(tmp_path):
    manager = _manager(tmp_path / "m.db")
    _, host_first = _response(manager)
    _, host_second = _response(manager)

    assert host_first.call_count == 1
    assert host_second.call_count == 1
    assert manager.get_migration_status.call_count == 1


def test_warmup_primes_cache_so_first_request_is_served_from_it(tmp_path, monkeypatch):
    manager = _manager(tmp_path / "m.db")
    import src.api as api

    monkeypatch.setattr(api, "_db", object())
    monkeypatch.setattr(route, "MigrationManager", lambda db: manager)

    route._warm_migration_list_cache()
    assert manager.get_migration_status.call_count == 1

    # The first real request reuses the warmed snapshot instead of rebuilding.
    result, _ = _response(manager)
    assert len(result.migrations) == 3
    assert manager.get_migration_status.call_count == 1


def test_cache_is_scoped_per_database(tmp_path):
    first_manager = _manager(tmp_path / "a.db", applied=1)
    second_manager = _manager(tmp_path / "b.db", applied=5)
    second_manager.get_migration_status.return_value["applied_migrations"] = [
        f"{i:03d}" for i in range(5)
    ]

    first, _ = _response(first_manager)
    second, _ = _response(second_manager)

    assert len(first.migrations) == 1
    assert len(second.migrations) == 5
    assert first_manager.get_migration_status.call_count == 1
    assert second_manager.get_migration_status.call_count == 1


def test_router_lifespan_primes_cache_on_startup(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    calls = []
    monkeypatch.setattr(route, "_warm_migration_list_cache", lambda: calls.append(True))

    app = FastAPI()
    app.include_router(route.router)
    with TestClient(app):
        pass

    assert calls == [True]


def test_cache_is_bounded_and_metadata_lookups_stay_linear(tmp_path):
    manager = _manager(tmp_path / "m.db", applied=12)
    _response(manager)

    assert len(route._list_cache) == 1

    # Metadata is parsed once per distinct version (no repeated/N+2 lookups).
    assert manager.tracker.get_migration_metadata.call_count == 12

    # The cache never grows past its bound even with many distinct DBs.
    for i in range(route._LIST_CACHE_MAX + 5):
        other = _manager(tmp_path / f"db-{i}.db")
        _response(other)
    assert len(route._list_cache) <= route._LIST_CACHE_MAX
