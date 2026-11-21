"""Regression tests for the four fast public/diagnostic endpoints.

Covers the performance + security contract of ``/status``, ``/version``,
``/ready`` and ``/admin/capabilities``:

* output is unchanged (the caches/memoisation are transparent);
* static/capability data is computed once, not per request (perf);
* responses are bounded and errors never leak internals (security).
"""

import asyncio
import importlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request, Response

import src.api.routes.admin.capabilities as admin_caps
import src.api.routes.capabilities as cap_routes
import src.api.routes.health as health
import src.api.routes.version as version_routes
from src.core.artifacts.capabilities import CapabilityInfo, CapabilityState

# ``src.api.routes`` rebinds the name ``config`` to utils.config, so a plain
# ``import src.api.routes.config as x`` would resolve to the wrong module.
config_routes = importlib.import_module("src.api.routes.config")


@pytest.fixture(autouse=True)
def _isolate_caches():
    """Every test starts and ends with cold caches."""

    def clear():
        version_routes._cached_server_version = None
        admin_caps.clear_admin_capabilities_payload_cache()
        cap_routes.clear_capabilities_payload_cache()
        config_routes.clear_capabilities_snapshot_cache()

    clear()
    yield
    clear()


# --------------------------------------------------------------------------
# Identical output
# --------------------------------------------------------------------------


def test_ready_static_body_and_no_store_header():
    resp = asyncio.run(health.readiness_probe(Response()))
    assert isinstance(resp, Response)
    assert resp.status_code == 200
    assert resp.body == b'{"status":"ready"}'
    assert resp.media_type == "application/json"
    assert resp.headers["cache-control"] == "no-store"


def test_version_payload_matches_fresh_model():
    first = version_routes.get_server_version()
    expected = version_routes.ServerVersionResponse(
        version=version_routes._version_to_info(version_routes.version_util.current()),
        min_supported_version=(
            version_routes._version_to_info(version_routes.version_util.min_supported())
            if version_routes.version_util.min_supported()
            else None
        ),
        api_version="v1",
    )
    assert first.model_dump() == expected.model_dump()
    assert first.api_version == "v1"
    assert first.version.string == version_routes.version_util.current_string()


def test_status_payload_matches_fresh_model():
    start = version_routes._server_start_time
    version_routes._server_start_time = start - 42
    try:
        status = version_routes.get_server_status()
    finally:
        version_routes._server_start_time = start
    assert status.state == version_routes.get_server_state()
    assert status.version.string == version_routes.version_util.current_string()
    assert status.uptime_seconds is not None and status.uptime_seconds >= 42


# --------------------------------------------------------------------------
# Performance: static/capability data is computed once
# --------------------------------------------------------------------------


def test_version_payload_is_memoized():
    # Populate the cache first, then make the source raise. A second call must
    # be served from the memo and must not re-read the version source.
    first = version_routes.get_server_version()
    with pytest.MonkeyPatch.context() as m:
        m.setattr(version_routes.version_util, "min_supported", lambda: 1 / 0)
        assert version_routes.get_server_version() is first


def test_config_snapshot_avoids_repeated_config_reads(monkeypatch):
    calls = {"n": 0}
    real = config_routes.config.get

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(config_routes.config, "get", counting)
    asyncio.run(config_routes.get_capabilities())
    first = calls["n"]
    assert first > 0
    asyncio.run(config_routes.get_capabilities())
    assert calls["n"] == first, "config was re-read on a warm snapshot"


def test_admin_capabilities_evaluated_only_once_per_ttl(monkeypatch):
    calls = {"n": 0}
    real = admin_caps.get_artifact_capabilities

    def counting():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(admin_caps, "get_artifact_capabilities", counting)
    monkeypatch.setattr(admin_caps, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(admin_caps, "require_admin_permission", lambda r, p: 1)

    scope = {"type": "http", "headers": [], "client": ("127.0.0.1", 0)}
    request = Request(scope)
    for _ in range(25):
        asyncio.run(admin_caps.get_admin_capabilities(request))
    assert calls["n"] == 1, "capability catalog was re-evaluated on a warm cache"


def test_admin_capabilities_cache_can_be_invalidated(monkeypatch):
    calls = {"n": 0}
    real = admin_caps.get_artifact_capabilities

    def counting():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(admin_caps, "get_artifact_capabilities", counting)
    admin_caps._build_payload()
    admin_caps._build_payload()
    assert calls["n"] == 2  # _build_payload always evaluates
    admin_caps.clear_admin_capabilities_payload_cache()
    admin_caps._get_payload()
    admin_caps._get_payload()
    assert calls["n"] == 3  # cache collapsed the two _get_payload calls


# --------------------------------------------------------------------------
# Security: bounded responses and generic errors
# --------------------------------------------------------------------------


def test_admin_capabilities_are_bounded(monkeypatch):
    oversized = {
        "f" * 500: CapabilityInfo(
            feature="x" * 500,
            state=CapabilityState.AVAILABLE,
            message="M" * 5000,
            details={"d" * 500: "V" * 5000, "ok": True},
        )
    }
    for i in range(admin_caps.MAX_CAPABILITIES + 25):
        oversized[f"feature-{i}"] = CapabilityInfo(
            feature=f"feature-{i}",
            state=CapabilityState.AVAILABLE,
            message="ok",
            details={},
        )
    monkeypatch.setattr(admin_caps, "get_artifact_capabilities", lambda: oversized)
    monkeypatch.setattr(admin_caps, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(admin_caps, "require_admin_permission", lambda r, p: 1)

    result = asyncio.run(
        admin_caps.get_admin_capabilities(
            Request({"type": "http", "headers": [], "client": ("127.0.0.1", 0)})
        )
    )
    assert len(result["capabilities"]) <= admin_caps.MAX_CAPABILITIES
    for name, payload in result["capabilities"].items():
        assert len(name) <= admin_caps.MAX_FEATURE_LENGTH
        assert len(payload["message"]) <= admin_caps.MAX_MESSAGE_LENGTH
        for key, value in payload["details"].items():
            assert len(key) <= admin_caps.MAX_FEATURE_LENGTH
            if isinstance(value, str):
                assert len(value) <= admin_caps.MAX_DETAIL_VALUE_LENGTH
    assert result["summary"]["total"] == len(result["capabilities"])


def test_admin_capabilities_enforces_auth_gates(monkeypatch):
    seen = {"host": 0, "scope": 0}

    def host(_r):
        seen["host"] += 1
        raise HTTPException(status_code=403, detail="denied")

    def scope(_r, _p):
        seen["scope"] += 1

    monkeypatch.setattr(admin_caps, "check_host_restriction", host)
    monkeypatch.setattr(admin_caps, "require_admin_permission", scope)
    with pytest.raises(HTTPException):
        asyncio.run(
            admin_caps.get_admin_capabilities(
                Request({"type": "http", "headers": [], "client": ("127.0.0.1", 0)})
            )
        )
    assert seen == {"host": 1, "scope": 0}


def test_admin_capabilities_error_is_generic(monkeypatch):
    secret = "/srv/private/config.yaml:super-secret"

    def boom():
        raise RuntimeError(secret)

    monkeypatch.setattr(admin_caps, "_get_payload", boom)
    monkeypatch.setattr(admin_caps, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(admin_caps, "require_admin_permission", lambda r, p: 1)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            admin_caps.get_admin_capabilities(
                Request({"type": "http", "headers": [], "client": ("127.0.0.1", 0)})
            )
        )
    assert exc.value.status_code == 500
    assert secret not in str(exc.value.detail)


def test_client_capabilities_error_is_generic(monkeypatch):
    secret = "/srv/private/voice/provider.key"

    def boom():
        raise RuntimeError(secret)

    monkeypatch.setattr(cap_routes, "_get_capabilities_payload", boom)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            cap_routes.get_capabilities(current_user=SimpleNamespace(user_id=1))
        )
    assert exc.value.status_code == 500
    assert secret not in str(exc.value.detail)
