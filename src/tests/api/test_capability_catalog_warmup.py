"""Regression: capability catalogs are warmed at boot, off the request path.

The launcher and admin catalogs share a cold first-build cost: every evaluator
runs, including the voice-transcription provider probe that imports the provider
factory. A background warm-up now primes both single-entry, TTL-bounded caches
on a worker thread so the first real, authenticated request is a hit. These
tests pin the warm-up wiring, its off-request execution, and - critically - that
the auth gates still run on every request even when the payload is cached.
"""

import asyncio
import threading
import time

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api import warmup
import src.api.routes.admin.capabilities as admin_caps
import src.api.routes.capabilities as caps
from src.api.middleware.authentication import get_current_user
from src.core.artifacts.capabilities import CapabilityInfo, CapabilityState


def _info(name, state=CapabilityState.AVAILABLE):
    return CapabilityInfo(feature=name, state=state, message="ok", details={})


@pytest.fixture(autouse=True)
def _cold_caches():
    caps.clear_capabilities_payload_cache()
    admin_caps.clear_admin_capabilities_payload_cache()
    yield
    caps.clear_capabilities_payload_cache()
    admin_caps.clear_admin_capabilities_payload_cache()


def test_warm_capability_catalogs_primes_both_caches(monkeypatch):
    calls = []

    def slow_catalog():
        calls.append(1)
        time.sleep(0.02)  # stand-in for the cold provider probe/import
        return {"editor": _info("editor")}

    monkeypatch.setattr(caps, "get_artifact_capabilities", slow_catalog)
    monkeypatch.setattr(admin_caps, "get_artifact_capabilities", slow_catalog)

    warmup._warm_capability_catalogs()
    assert len(calls) == 2  # one full evaluation per cache

    # Both caches are now warm: a subsequent read is a cheap hit, and both
    # payloads are identical to a freshly built one.
    t0 = time.perf_counter()
    launcher = caps._get_capabilities_payload()
    admin = admin_caps._get_payload()
    elapsed = time.perf_counter() - t0

    assert len(calls) == 2
    assert elapsed < 0.02  # two cold builds would cost >= 0.04s
    assert launcher["editor"]["state"] == "available"
    assert admin["capabilities"]["editor"]["state"] == "available"


def test_warm_once_invokes_catalog_warmup(monkeypatch):
    called = []
    monkeypatch.setattr(
        warmup, "_warm_capability_catalogs", lambda: called.append(True)
    )

    assert warmup.warm_once() >= 0.0
    assert called == [True]


def test_warmup_loop_runs_warm_once_in_a_worker_thread(monkeypatch):
    monkeypatch.setattr(warmup, "_warmup_enabled", lambda: True)
    monkeypatch.setattr(warmup, "_warmup_delay", lambda: 0.0)
    seen = {}

    def record_thread(app=None):
        seen["thread"] = threading.current_thread()
        return 0.0

    monkeypatch.setattr(warmup, "warm_once", record_thread)
    asyncio.run(warmup.warmup_loop())

    assert "thread" in seen
    assert seen["thread"] is not threading.main_thread()


def test_catalog_warmup_is_non_fatal(monkeypatch):
    def boom():
        raise RuntimeError("evaluator exploded")

    monkeypatch.setattr(caps, "_build_capabilities_payload", boom)
    monkeypatch.setattr(admin_caps, "_build_payload", boom)

    # A broken evaluator must be logged and skipped, never raised into startup.
    warmup._warm_capability_catalogs()


def test_catalog_warmup_is_idempotent_within_ttl(monkeypatch):
    calls = []
    monkeypatch.setattr(
        caps,
        "get_artifact_capabilities",
        lambda: calls.append(1) or {"editor": _info("editor")},
    )
    monkeypatch.setattr(
        admin_caps,
        "get_artifact_capabilities",
        lambda: {"editor": _info("editor")},
    )

    warmup._warm_capability_catalogs()
    warmup._warm_capability_catalogs()

    assert calls == [1]


def test_first_request_uses_warm_payload_but_auth_gate_still_runs(monkeypatch):
    evaluations = []
    monkeypatch.setattr(
        caps,
        "get_artifact_capabilities",
        lambda: evaluations.append(1) or {"editor": _info("editor")},
    )

    # Prime through the boot warm-up path, not through a request.
    warmup._warm_capability_catalogs()
    assert evaluations == [1]

    app = FastAPI()
    app.include_router(caps.router)
    auth_calls = []

    def fake_user():
        auth_calls.append(1)
        return object()

    app.dependency_overrides[get_current_user] = fake_user

    with TestClient(app) as client:
        response = client.get("/capabilities/artifacts")

    assert response.status_code == 200
    assert response.json()["editor"]["state"] == "available"
    # The first real request hit the warmed cache; no cold re-evaluation.
    assert evaluations == [1]
    # ...but it still passed through the authentication dependency.
    assert auth_calls == [1]


def test_auth_rejection_still_applies_with_a_warm_payload(monkeypatch):
    monkeypatch.setattr(
        caps, "get_artifact_capabilities", lambda: {"editor": _info("editor")}
    )
    warmup._warm_capability_catalogs()

    app = FastAPI()
    app.include_router(caps.router)

    def deny():
        raise HTTPException(status_code=401, detail="denied")

    app.dependency_overrides[get_current_user] = deny

    with TestClient(app) as client:
        response = client.get("/capabilities/artifacts")

    assert response.status_code == 401


def test_admin_gates_still_run_on_a_warm_payload(monkeypatch):
    monkeypatch.setattr(
        admin_caps, "get_artifact_capabilities", lambda: {"editor": _info("editor")}
    )
    warmup._warm_capability_catalogs()

    gate_calls = []
    monkeypatch.setattr(
        admin_caps,
        "check_host_restriction",
        lambda request: gate_calls.append("host"),
    )
    monkeypatch.setattr(
        admin_caps,
        "require_admin_permission",
        lambda request, perm: gate_calls.append(f"scope:{perm}"),
    )

    app = FastAPI()
    app.include_router(admin_caps.router)

    with TestClient(app) as client:
        response = client.get("/capabilities")

    assert response.status_code == 200
    assert gate_calls == ["host", "scope:config.read"]
