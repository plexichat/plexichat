"""Regression tests for the admin permission/role catalog cache warm-up.

The first ``GET /admin/roles`` after a restart used to pay for the whole role
query plus per-row serialization (each role carries its permission map) while
holding a request open. The rendered page is cached in ``core.admin.read_cache``
and is now primed from the role router's lifespan, off the event loop.

These tests pin the payoff (a measurably cheaper warm hit), the wiring (the
warm-up primes the exact entry the first request asks for), and the invariants
that must survive it: the host/scope gates still run on every request, a role
mutation still drops the warmed entry, and a broken database or builder can
never turn the warm-up into a startup failure.
"""

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.admin import roles as route
from src.core.admin import read_cache

pytestmark = pytest.mark.unit

BIG = 9007199254740993  # past 2^53


def _role_row():
    return {
        "id": BIG,
        "name": "support_admin",
        "description": "d",
        "permissions": '{"admin.roles":true}',
        "created_at": 1,
        "created_by": BIG + 1,
        "updated_at": None,
        "is_system": 0,
    }


class _RoleDB:
    """Minimal DB double: counts role queries and returns one role row."""

    def __init__(self):
        self.config = {"path": ":memory:"}
        self.fetch_calls = 0

    def fetch_all(self, query, params=None):
        self.fetch_calls += 1
        return [_role_row()]

    def fetch_one(self, query, params=None):
        return None


def _burn():
    """Deterministic stand-in for the cold query/serialize cost (no sleeping)."""
    total = 0
    for i in range(200_000):
        total = (total + i * i) % 1_000_003
    return total


@pytest.fixture(autouse=True)
def _clean_cache():
    read_cache.clear_all()
    _ROLE_DB.fetch_calls = 0
    yield
    read_cache.clear_all()


def _call_route(request=None, *, scope_error=False):
    """Run list_roles with its gates stubbed, returning (response, gate mocks)."""
    if request is None:
        request = MagicMock()

    def _gate(req, *args, **kwargs):
        if scope_error:
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="no scope")
        return 7

    patches = [
        patch.object(route, "check_host_restriction"),
        patch.object(route, "_require_role_manager", side_effect=_gate),
        patch.object(api, "get_db", return_value=_ROLE_DB),
    ]
    mocks = [p.start() for p in patches]
    try:
        # Pass explicit ints: calling the function directly bypasses FastAPI's
        # query parsing, so the ``Query`` defaults would otherwise be the key.
        result = asyncio.run(
            route.list_roles(
                request,
                limit=read_cache.ROLE_CATALOG_DEFAULT_LIMIT,
                offset=read_cache.ROLE_CATALOG_DEFAULT_OFFSET,
            )
        )
    finally:
        for p in reversed(patches):
            p.stop()
    return result, mocks


# module-level db so the warm-up and the route resolve the same cache-token.
_ROLE_DB = _RoleDB()


# --------------------------------------------------------------------------- #
# cold / warm measurement
# --------------------------------------------------------------------------- #


def test_cold_first_hit_costs_the_build_warm_hit_skips_it():
    real_render = route._render_role_catalog
    calls = []

    def slow_render(db, limit, offset):
        calls.append((limit, offset))
        _burn()
        return real_render(db, limit, offset)

    with (
        patch.object(api, "get_db", return_value=_ROLE_DB),
        patch.object(route, "_render_role_catalog", side_effect=slow_render),
    ):
        t0 = time.perf_counter()
        cold = read_cache.warm_role_catalog(
            lambda: route._render_role_catalog(
                _ROLE_DB,
                read_cache.ROLE_CATALOG_DEFAULT_LIMIT,
                read_cache.ROLE_CATALOG_DEFAULT_OFFSET,
            )
        )
        cold_elapsed = time.perf_counter() - t0

        t0 = time.perf_counter()
        warm = read_cache.warm_role_catalog(
            lambda: route._render_role_catalog(
                _ROLE_DB,
                read_cache.ROLE_CATALOG_DEFAULT_LIMIT,
                read_cache.ROLE_CATALOG_DEFAULT_OFFSET,
            )
        )
        warm_elapsed = time.perf_counter() - t0

    # Correctness: the warm hit is byte-for-byte the cold payload.
    assert warm == cold
    assert b"support_admin" in cold

    # Performance: the expensive builder ran once and the warm hit skipped it.
    assert len(calls) == 1
    assert _ROLE_DB.fetch_calls == 1
    assert warm_elapsed < cold_elapsed / 2, (
        f"warm hit was not cheaper: cold={cold_elapsed:.4f}s warm={warm_elapsed:.4f}s"
    )


# --------------------------------------------------------------------------- #
# warm-up wiring
# --------------------------------------------------------------------------- #


def test_warmup_primes_cache_so_first_request_is_served_from_it():
    real_render = route._render_role_catalog
    renders = []
    _ROLE_DB.fetch_calls = 0

    def counting_render(db, limit, offset):
        renders.append((limit, offset))
        return real_render(db, limit, offset)

    with (
        patch.object(api, "get_db", return_value=_ROLE_DB),
        patch.object(route, "_render_role_catalog", side_effect=counting_render),
    ):
        route.warm_admin_permission_catalog_cache()
        assert renders == [
            (
                read_cache.ROLE_CATALOG_DEFAULT_LIMIT,
                read_cache.ROLE_CATALOG_DEFAULT_OFFSET,
            )
        ]
        assert _ROLE_DB.fetch_calls == 1
        after_warm = _ROLE_DB.fetch_calls

        response, _ = _call_route()

        assert response.status_code == 200
        assert b"support_admin" in response.body
        assert _ROLE_DB.fetch_calls == after_warm, (
            "first request must hit the warm cache"
        )
        assert len(renders) == 1


def test_warmup_is_idempotent_within_ttl():
    with patch.object(api, "get_db", return_value=_ROLE_DB):
        route.warm_admin_permission_catalog_cache()
        route.warm_admin_permission_catalog_cache()
    assert _ROLE_DB.fetch_calls == 1


def test_lifespan_runs_warmup_off_the_event_loop():
    threads = []
    main_thread = threading.get_ident()

    def _record():
        threads.append(threading.get_ident())

    with patch.object(route, "warm_admin_permission_catalog_cache", _record):

        async def _run():
            async with route._role_catalog_lifespan(object()):
                pass

        asyncio.run(_run())

    assert threads and threads[0] != main_thread


# --------------------------------------------------------------------------- #
# security / correctness invariants
# --------------------------------------------------------------------------- #


def test_host_and_scope_gates_still_run_on_a_warm_hit():
    with patch.object(api, "get_db", return_value=_ROLE_DB):
        route.warm_admin_permission_catalog_cache()

    response, patches = _call_route()
    assert response.status_code == 200
    host_gate, scope_gate = patches[0], patches[1]
    assert host_gate.call_count == 1
    assert scope_gate.call_count == 1

    # A denied scope gate still refuses the request even though the payload is
    # cached -- the warm-up never turns into an authorization bypass.
    with pytest.raises(HTTPException) as denied:
        _call_route(scope_error=True)
    assert denied.value.status_code == 403


def test_role_mutation_invalidation_drops_the_warmed_entry():
    real_render = route._render_role_catalog
    renders = []

    def counting_render(db, limit, offset):
        renders.append(1)
        return real_render(db, limit, offset)

    with (
        patch.object(api, "get_db", return_value=_ROLE_DB),
        patch.object(route, "_render_role_catalog", side_effect=counting_render),
    ):
        route.warm_admin_permission_catalog_cache()
        assert len(renders) == 1

        read_cache.invalidate_admin_read_caches()
        route.warm_admin_permission_catalog_cache()
        assert len(renders) == 2, "a mutation must make the next read cold again"


def test_warmup_is_non_fatal_without_a_database():
    with patch.object(api, "get_db", side_effect=RuntimeError("no db")):
        route.warm_admin_permission_catalog_cache()  # must not raise


def test_warmup_is_non_fatal_when_the_render_fails():
    with (
        patch.object(api, "get_db", return_value=_ROLE_DB),
        patch.object(route, "_render_role_catalog", side_effect=ValueError("bad rows")),
    ):
        route.warm_admin_permission_catalog_cache()  # must not raise

    # Nothing was cached, so the request path will build the entry on demand.
    response, _ = _call_route()
    assert response.status_code == 200
    assert b"support_admin" in response.body
