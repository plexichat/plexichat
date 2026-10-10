"""
Tests for the admin device-trust & session-duration overview.

Covers: effective-policy resolution (defaults, trusted override, disabled
override), per-user aggregation (device trust, session policy kinds), totals,
and that the admin router exposes the endpoint behind the right scope.
"""

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.admin.device_overview import (
    _sessions_policy,
    get_device_trust_overview,
)


def _mgr(auth_manager):
    from src.core.auth._lazy import _get_auth_manager

    return _get_auth_manager().get_instance()


def _register_and_login(auth_manager, username: str, fp: str):
    from unittest.mock import patch
    from src.utils import encryption

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username,
            email=f"{username}@example.com",
            password="TestPass123!",
        )
    with patch.object(encryption, "verify_password", return_value=True):
        auth_manager.login(username, "TestPass123!", device_info={"fingerprint": fp})
    return user


class TestSessionsPolicy:
    def test_defaults_applied_when_block_missing(self):
        policy = _sessions_policy({})
        assert policy["expire_hours"] == 168
        assert policy["trusted_expire_hours"] == 720
        assert policy["trusted_override_enabled"] is True
        assert policy["effective_trusted_expire_hours"] == 720
        assert policy["extend_on_activity"] is True

    def test_trusted_override_respected(self):
        policy = _sessions_policy(
            {"sessions": {"expire_hours": 24, "trusted_expire_hours": 720}}
        )
        assert policy["expire_hours"] == 24
        assert policy["trusted_expire_hours"] == 720
        assert policy["trusted_override_enabled"] is True
        assert policy["effective_trusted_expire_hours"] == 720

    def test_null_override_disables_trusted_duration(self):
        policy = _sessions_policy(
            {"sessions": {"expire_hours": 24, "trusted_expire_hours": None}}
        )
        assert policy["trusted_override_enabled"] is False
        assert policy["effective_trusted_expire_hours"] == 24


class TestDeviceTrustOverview:
    def test_overview_flags_trusted_device_sessions(self, db, auth_manager):
        user = _register_and_login(auth_manager, "ov_trust", "ov_fp")
        mgr = _mgr(auth_manager)
        mgr._config["sessions"] = {
            "expire_hours": 24,
            "trusted_expire_hours": 720,
            "extend_on_activity": True,
            "extend_threshold_hours": 24,
        }
        assert auth_manager.trust_device(user.id, "ov_fp") is True

        overview = get_device_trust_overview(mgr, mgr._config)

        assert overview["totals"]["users"] >= 1
        assert overview["totals"]["trusted_devices"] >= 1
        entry = next(u for u in overview["users"] if u["user_id"] == str(user.id))
        assert entry["trusted_device_count"] == 1
        assert entry["session_count"] >= 1
        # The session was created while the device was already trusted, so it
        # reports the trusted policy.
        trusted_sessions = [s for s in entry["sessions"] if s["device_trusted"]]
        assert len(trusted_sessions) >= 1
        assert trusted_sessions[0]["policy_kind"] == "trusted"
        assert trusted_sessions[0]["duration_hours"] == 720

    def test_overview_untrusted_sessions_use_default_policy(self, db, auth_manager):
        user = _register_and_login(auth_manager, "ov_untrust", "ov_un_fp")
        mgr = _mgr(auth_manager)
        mgr._config["sessions"] = {
            "expire_hours": 24,
            "trusted_expire_hours": 720,
        }

        overview = get_device_trust_overview(mgr, mgr._config)
        entry = next(u for u in overview["users"] if u["user_id"] == str(user.id))
        assert entry["trusted_device_count"] == 0
        assert entry["session_count"] >= 1
        for s in entry["sessions"]:
            assert s["device_trusted"] is False
            assert s["policy_kind"] == "default"
            assert s["duration_hours"] == 24

    def test_overview_disabled_override_reports_default_for_trusted(
        self, db, auth_manager
    ):
        user = _register_and_login(auth_manager, "ov_off", "ov_off_fp")
        mgr = _mgr(auth_manager)
        mgr._config["sessions"] = {
            "expire_hours": 24,
            "trusted_expire_hours": None,
        }
        assert auth_manager.trust_device(user.id, "ov_off_fp") is True

        overview = get_device_trust_overview(mgr, mgr._config)
        assert overview["policy"]["trusted_override_enabled"] is False
        entry = next(u for u in overview["users"] if u["user_id"] == str(user.id))
        for s in entry["sessions"]:
            # Trust exists on the device, but the override is off: default TTL.
            assert s["duration_hours"] == 24

    def test_totals_are_consistent(self, db, auth_manager):
        user_a = _register_and_login(auth_manager, "ov_tot_a", "ov_tot_a_fp")
        _register_and_login(auth_manager, "ov_tot_b", "ov_tot_b_fp")
        mgr = _mgr(auth_manager)
        auth_manager.trust_device(user_a.id, "ov_tot_a_fp")

        overview = get_device_trust_overview(mgr, mgr._config)
        t = overview["totals"]
        assert t["users"] == sum(1 for _ in overview["users"])
        assert t["devices"] == sum(len(u["devices"]) for u in overview["users"])
        assert t["sessions"] == sum(u["session_count"] for u in overview["users"])
        assert t["trusted_devices"] >= 1

    def test_no_raw_fingerprints_in_overview(self, db, auth_manager):
        import json

        user = _register_and_login(auth_manager, "ov_sec", "ov_secret_fp_123")
        assert user.id is not None
        mgr = _mgr(auth_manager)
        overview = get_device_trust_overview(mgr, mgr._config)
        # The raw fingerprint must never appear anywhere in the payload.
        assert "ov_secret_fp_123" not in json.dumps(overview)


class TestAdminRouteRegistered:
    def test_route_mounted_with_scope_guard(self):
        """The admin router must expose the overview behind the devices scope."""
        from src.api.routes.admin import router
        from src.api.routes.admin.device_trust import get_device_trust_admin_overview

        # include_router() wraps sub-routers in opaque objects that hide the
        # concrete paths, so recurse through original_router to collect them.
        def _walk(obj):
            for r in getattr(obj, "routes", []):
                p = getattr(r, "path", None)
                if p:
                    yield p
                else:
                    orig = getattr(r, "original_router", None)
                    if orig is not None and orig is not obj:
                        yield from _walk(orig)

        paths = set(_walk(router))
        assert "/device-trust/overview" in paths

        # The handler checks the admin.security.devices permission.
        import inspect

        source = inspect.getsource(get_device_trust_admin_overview)
        assert 'require_admin_permission(request, "admin.security.devices")' in source

    def test_route_serves_string_snowflake_ids_end_to_end(self, db, auth_manager):
        """GET /admin/device-trust/overview must emit ids as strings.

        Runs the real route and the real aggregation helper against a seeded
        user/device/session, pinning the contract at the HTTP boundary (the
        schema previously coerced the helper's strings back to numbers).
        """
        from src.api.routes.admin import device_trust as device_trust_routes
        from src.api.routes.admin import router as admin_router

        user = _register_and_login(auth_manager, "ov_route", "ov_route_fp")
        mgr = _mgr(auth_manager)
        mgr._config["sessions"] = {
            "expire_hours": 24,
            "trusted_expire_hours": 720,
        }
        assert auth_manager.trust_device(user.id, "ov_route_fp") is True

        app = FastAPI()
        # The overview is registered directly on the admin router (not via a
        # prefixed sub-router), so include the full router.
        app.include_router(admin_router, prefix="/api/v1/admin")
        client = TestClient(app)
        patches = (
            patch.object(device_trust_routes, "check_host_restriction"),
            patch.object(
                device_trust_routes, "require_admin_permission", return_value=1
            ),
        )
        for p in patches:
            p.start()
        try:
            response = client.get("/api/v1/admin/device-trust/overview")
        finally:
            for p in reversed(patches):
                p.stop()

        assert response.status_code == 200, response.text
        body = response.json()
        entry = next(u for u in body["users"] if u["user_id"] == str(user.id))
        assert isinstance(entry["user_id"], str)
        assert entry["devices"], "expected the seeded device"
        assert isinstance(entry["devices"][0]["id"], str)
        assert entry["sessions"], "expected the seeded session"
        assert isinstance(entry["sessions"][0]["id"], str)
        # Timestamps/counts stay numeric.
        assert isinstance(body["totals"]["users"], int)

    def test_schema_importable_from_admin_package(self):
        from src.api.schemas.admin import DeviceTrustOverviewResponse

        # Round-trip a minimal payload through the schema.
        resp = DeviceTrustOverviewResponse(
            policy={
                "expire_hours": 168,
                "trusted_expire_hours": 720,
                "trusted_override_enabled": True,
                "effective_trusted_expire_hours": 720,
                "extend_on_activity": True,
                "extend_threshold_hours": 24,
                "trusted_extend_threshold_hours": 168,
            },
            users=[],
            totals={
                "users": 0,
                "devices": 0,
                "trusted_devices": 0,
                "sessions": 0,
                "active_sessions": 0,
                "trusted_device_sessions": 0,
            },
        )
        assert resp.totals.users == 0
