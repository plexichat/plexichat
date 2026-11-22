"""A failed license reload must not leave stale capability grants in place.

``POST /api/v1/admin/license/check`` reloads the on-disk/env license and may
change the effective license state even when the reload *fails* (the manager is
left holding the newly loaded, invalid license and the module is demoted to
free tier). The route only invalidated the derived capability caches on the
success path, so a reload that revoked a premium feature still left the
capability gate reporting ``AVAILABLE`` from its process-local memo. Every
artifact/Suite gate that routes through ``capability_allows_artifact`` kept
granting the revoked entitlement until the short TTL expired.

This test drives the real ``reload_license`` with an invalid on-disk license
and the real admin route, and asserts the gate fails closed on the very next
call after the failed reload. It is non-vacuous: it first proves the capability
was genuinely AVAILABLE under the valid license and that the reload genuinely
revoked it before checking the gate.
"""

import asyncio
import base64
import time

import pytest
from fastapi import HTTPException

import utils.licensing as licensing
from utils.licensing.core import generate_keypair, sign_license
from src.api.routes.admin import licensing as route
from src.core.artifacts import capabilities as caps

pytestmark = [pytest.mark.security, pytest.mark.unit]


def _signed_license(priv: bytes, *, features: dict) -> dict:
    data = {
        "version": "1.0",
        "instance_id": "instance-1",
        "issued_at": int(time.time()) - 100,
        "expires_at": None,  # perpetual: no time-based rescue
        "features": features,
        "limits": {},
    }
    data["signature"] = sign_license(priv, data)
    return data


def _invalid_license() -> dict:
    """Structurally valid, cryptographically bogus signature."""
    return {
        "version": "1.0",
        "instance_id": "attacker",
        "issued_at": int(time.time()) - 100,
        "expires_at": None,
        "features": {"artifacts_whiteboard": True},
        "limits": {},
        "signature": "A" * 88,
    }


@pytest.fixture
def isolated_state(monkeypatch):
    """Save/restore the process-wide licensing globals around each test."""
    saved = (
        licensing._license_manager,
        licensing._free_tier_mode,
        licensing._setup_called,
    )
    caps.clear_capability_cache()
    try:
        yield
    finally:
        (
            licensing._license_manager,
            licensing._free_tier_mode,
            licensing._setup_called,
        ) = saved
        caps.clear_capability_cache()


def test_failed_reload_fail_closed_at_capability_gate(isolated_state, monkeypatch):
    priv, pub = generate_keypair()
    licensing.setup(
        public_key_base64=base64.b64encode(pub).decode("ascii"),
        license_path="/tmp/opencode/nonexistent-license",
    )
    assert (
        licensing.apply_license_from_dict(
            _signed_license(priv, features={"artifacts_whiteboard": True})
        )["success"]
        is True
    )

    # Prime the capability memo under the valid, premium-granting license.
    caps.clear_capability_cache()
    assert (
        caps.get_capability("artifacts_whiteboard").state
        == caps.CapabilityState.AVAILABLE
    )
    assert caps.capability_allows_artifact("whiteboard") is True

    # The admin edits the license file to an invalid license and hits /check.
    # reload_license() picks it up and validation fails -> real state change.
    def fake_try_load():
        licensing._license_manager.load_from_dict(_invalid_license())
        licensing._license_manager.validate()
        return True

    monkeypatch.setattr(licensing, "_try_load_license", fake_try_load)
    monkeypatch.setattr(route, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(route, "require_admin_permission", lambda request, scope: 1)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(route.force_validate_license(None))
    assert exc_info.value.status_code == 400

    # Non-vacuous: the reload genuinely revoked the entitlement.
    assert licensing.is_free_tier() is True
    assert licensing.has_feature("artifacts_whiteboard", default=False) is False

    # FAIL OPEN pre-fix: the memo still reports AVAILABLE and gates keep
    # granting the revoked entitlement until the TTL lapses.
    info = caps.get_capability("artifacts_whiteboard")
    assert info.state != caps.CapabilityState.AVAILABLE
    assert caps.capability_allows_artifact("whiteboard") is False
