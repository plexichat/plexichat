"""The admin license feature catalogue is cached and warmed off-request.

``GET /admin/license/features`` used to rebuild a fixed 16-entry feature matrix
on every admin-panel poll, issuing three licensing accessors per feature (48
calls). Each accessor re-enters the license manager's validation, so the first
poll after a restart paid a cold cost and every later poll repeated the same
static work. The matrix is now held in a single-entry, TTL-bounded cache that is
primed at boot from the router lifespan (a worker thread).

These tests pin the wiring: warm-up primes the cache, the first real request
does no cold work, and both admin gates still run *before* the cache is read.
"""

import threading
import time

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api.routes.admin import licensing as route


_ACCESSOR_NAMES = ("has_feature", "get_feature_config", "get_feature_limit")


def _patch_accessors(monkeypatch, calls, delay=0.0):
    """Replace the three license accessors with deterministic recorders."""

    def recorder(name, value):
        def fake(key):
            calls.append((name, key))
            if delay:
                time.sleep(delay)
            return value

        return fake

    monkeypatch.setattr(
        route.licensing_module, "has_feature", recorder("has_feature", True)
    )
    monkeypatch.setattr(
        route.licensing_module,
        "get_feature_config",
        recorder("get_feature_config", {"max": 1}),
    )
    monkeypatch.setattr(
        route.licensing_module, "get_feature_limit", recorder("get_feature_limit", 5)
    )


def _allow(monkeypatch, gate_calls=None, host_deny=False, perm_deny=False):
    """Install no-op/denying admin gates and return a TestClient for the router."""

    def host(request):
        if gate_calls is not None:
            gate_calls.append("host")
        if host_deny:
            raise HTTPException(status_code=403, detail="host denied")

    def permission(request, scope):
        if gate_calls is not None:
            gate_calls.append(("permission", scope))
        if perm_deny:
            raise HTTPException(status_code=403, detail="permission denied")
        return 1

    monkeypatch.setattr(route, "check_host_restriction", host)
    monkeypatch.setattr(route, "require_admin_permission", permission)
    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1/admin")
    return TestClient(app)


@pytest.fixture(autouse=True)
def _cold_catalog():
    """Every test starts and ends with an empty catalogue."""
    route.clear_license_feature_catalog_cache()
    yield
    route.clear_license_feature_catalog_cache()


def test_warmup_primes_catalog_and_first_request_avoids_cold_work(monkeypatch):
    """Boot lifespan builds once; the first scoped poll is a cache hit."""
    calls = []
    # 16 features * 3 accessors * 2ms = ~96ms of simulated cold validation.
    _patch_accessors(monkeypatch, calls, delay=0.002)
    expected_calls = 3 * len(route._LICENSE_FEATURES)
    client = _allow(monkeypatch)

    with client:
        # The router lifespan ran during startup (in a worker thread) and primed
        # the single catalogue entry before __enter__ returned.
        assert len(calls) == expected_calls
        response = client.get("/api/v1/admin/license/features")

    assert response.status_code == 200, response.text
    assert len(response.json()["features"]) == 16
    # No cold re-evaluation on the first real request.
    assert len(calls) == expected_calls


def test_cold_vs_warm_timing(monkeypatch):
    """Measure the cold catalogue build against warmed cached reads."""
    calls = []
    _patch_accessors(monkeypatch, calls, delay=0.002)
    expected_calls = 3 * len(route._LICENSE_FEATURES)

    route.clear_license_feature_catalog_cache()
    t0 = time.perf_counter()
    route._get_license_feature_payload()
    cold_ms = (time.perf_counter() - t0) * 1000.0
    assert len(calls) == expected_calls

    rounds = 200
    t0 = time.perf_counter()
    for _ in range(rounds):
        route._get_license_feature_payload()
    warm_ms = ((time.perf_counter() - t0) * 1000.0) / rounds
    # Every warm read is a pure detached-cache hit: no licensing work at all.
    assert len(calls) == expected_calls

    print(
        f"\n  cold build: {cold_ms:.2f} ms | warm read: {warm_ms:.4f} ms | "
        f"speedup: {cold_ms / warm_ms:.1f}x"
    )
    # A cold build performs 48 delayed accessor calls; a warm read performs
    # none. The ratio is the point - the absolute numbers depend on the host.
    assert cold_ms > warm_ms * 10


def test_direct_warmup_primes_and_is_idempotent_within_ttl(monkeypatch):
    """Calling the warm-up directly primes the cache and repeats are hits."""
    calls = []
    _patch_accessors(monkeypatch, calls)
    expected = 3 * len(route._LICENSE_FEATURES)

    route.warm_license_feature_catalog_cache()
    assert len(calls) == expected
    route.warm_license_feature_catalog_cache()
    assert len(calls) == expected

    payload = route._get_license_feature_payload()
    assert len(calls) == expected
    assert payload["features"][0]["key"] == "bots"
    assert payload["features"][-1]["key"] == "all"


def test_admin_gates_still_run_on_a_warm_payload(monkeypatch):
    """A warm matrix never short-circuits the host or permission gate."""
    calls = []
    _patch_accessors(monkeypatch, calls)
    route.warm_license_feature_catalog_cache()

    gate_calls = []
    client = _allow(monkeypatch, gate_calls=gate_calls)
    with client:
        response = client.get("/api/v1/admin/license/features")

    assert response.status_code == 200, response.text
    assert "host" in gate_calls
    assert ("permission", route._LICENSE_SCOPE) in gate_calls


def test_permission_denial_never_reaches_the_cache(monkeypatch):
    """On a cold cache, a denied request performs no catalogue work at all."""
    calls = []
    _patch_accessors(monkeypatch, calls)
    # The lifespan warm-up is disabled here so the denial is the only event.
    monkeypatch.setattr(route, "warm_license_feature_catalog_cache", lambda: None)
    client = _allow(monkeypatch, perm_deny=True)

    with client:
        response = client.get("/api/v1/admin/license/features")

    assert response.status_code == 403
    assert calls == []
    assert route._feature_catalog_cache is None


def test_host_denial_never_reaches_the_cache(monkeypatch):
    """The host gate runs first and blocks the permission gate and the cache."""
    calls = []
    _patch_accessors(monkeypatch, calls)
    monkeypatch.setattr(route, "warm_license_feature_catalog_cache", lambda: None)
    gate_calls = []
    client = _allow(monkeypatch, gate_calls=gate_calls, host_deny=True)

    with client:
        response = client.get("/api/v1/admin/license/features")

    assert response.status_code == 403
    assert calls == []
    assert gate_calls == ["host"]


def test_router_lifespan_runs_warmup_off_the_event_loop(monkeypatch):
    """Startup warm-up executes in a worker thread, not the caller thread."""
    seen = {}

    def record():
        seen["thread"] = threading.current_thread()

    monkeypatch.setattr(route, "warm_license_feature_catalog_cache", record)
    app = FastAPI()
    app.include_router(route.router)

    with TestClient(app):
        pass

    assert "thread" in seen
    assert seen["thread"] is not threading.main_thread()


def test_warmup_is_non_fatal_on_a_broken_builder(monkeypatch):
    """A failing evaluator is logged and swallowed, never raised into boot."""

    def boom():
        raise RuntimeError("validator exploded")

    monkeypatch.setattr(route, "_build_license_feature_payload", boom)
    route.warm_license_feature_catalog_cache()  # must not raise
    assert route._feature_catalog_cache is None


def test_cached_response_matches_a_fresh_computation(monkeypatch):
    calls = []
    _patch_accessors(monkeypatch, calls)
    fresh = route._build_license_feature_payload()
    cached = route._get_license_feature_payload()

    assert cached == fresh
    assert set(cached) == {"features"}
    assert all(
        set(row) == {"key", "label", "enabled", "config", "limit"}
        for row in cached["features"]
    )


def test_cached_payload_is_detached_from_callers(monkeypatch):
    calls = []
    _patch_accessors(monkeypatch, calls)
    first = route._get_license_feature_payload()
    first["features"][0]["config"]["max"] = 999
    first["features"][0]["label"] = "tampered"
    first["features"].append({"key": "injected"})

    second = route._get_license_feature_payload()
    assert len(second["features"]) == 16
    assert second["features"][0]["config"] == {"max": 1}
    assert second["features"][0]["label"] == "Bot Platform"


def test_hot_swap_invalidation_refreshes_the_matrix(monkeypatch):
    state = {"enabled": True}

    def has_feature(key):
        return state["enabled"]

    monkeypatch.setattr(route.licensing_module, "has_feature", has_feature)
    monkeypatch.setattr(route.licensing_module, "get_feature_config", lambda key: None)
    monkeypatch.setattr(route.licensing_module, "get_feature_limit", lambda key: None)

    assert route._get_license_feature_payload()["features"][0]["enabled"] is True
    state["enabled"] = False
    # Without invalidation the old verdict is still cached.
    assert route._get_license_feature_payload()["features"][0]["enabled"] is True
    route.clear_license_feature_catalog_cache()
    assert route._get_license_feature_payload()["features"][0]["enabled"] is False


def test_cache_is_single_entry_bounded(monkeypatch):
    calls = []
    _patch_accessors(monkeypatch, calls)
    for _ in range(25):
        route._get_license_feature_payload()

    assert len(calls) == 3 * len(route._LICENSE_FEATURES)  # one build only
    cached = route._feature_catalog_cache
    assert isinstance(cached, tuple) and len(cached) == 2


def test_refreshes_after_ttl(monkeypatch):
    calls = []
    _patch_accessors(monkeypatch, calls)
    monkeypatch.setattr(route, "_FEATURE_CATALOG_TTL_SECONDS", 0.0)

    route._get_license_feature_payload()
    route._get_license_feature_payload()

    assert len(calls) == 2 * 3 * len(route._LICENSE_FEATURES)
