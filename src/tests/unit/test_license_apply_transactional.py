"""A failed license apply must not revoke a working license.

Regression for a live self-test downgrade:

    Action=license.apply Status=failed
    Details=License validation failed; staying in free tier

``apply_license_from_dict`` / ``apply_license_from_base64`` load the candidate
license into the manager *before* validating it. The old failure paths then set
``_free_tier_mode = True``, so a candidate with a bad signature left the manager
holding the bad license and the instance demoted to free tier — the previously
working license was gone (every PlexiJoin route then 403'd). A failed apply
must be transactional: the previous license and free-tier flag are restored.
"""

import base64
import json

import pytest

import src.utils.common_utils.utils.licensing as licensing


@pytest.fixture
def licensing_state():
    """Run a test against the real module, restoring its globals afterwards.

    ``licensing.setup()`` mutates process-wide module state. Saving and
    restoring ``(_license_manager, _free_tier_mode, _setup_called)`` in the
    ``finally`` keeps the suite order-independent.
    """
    saved = (
        licensing._license_manager,
        licensing._free_tier_mode,
        licensing._setup_called,
    )
    try:
        yield licensing
    finally:
        (
            licensing._license_manager,
            licensing._free_tier_mode,
            licensing._setup_called,
        ) = saved


def _invalid_license_dict() -> dict:
    """A structurally valid license whose Ed25519 signature is bogus.

    A real (wrong) signature is used rather than a mocked verifier: the payload
    parses cleanly, so it reaches signature verification and fails there.
    """
    return {
        "version": "1.0",
        "type": "standard",
        "instance_id": "not-the-real-issuer",
        "issued_at": 1,
        "expires_at": None,
        "features": {"plexijoin": True},
        "limits": {},
        "signature": base64.b64encode(b"not a real ed25519 signature").decode(),
    }


def _invalid_license_base64() -> str:
    """Base64 of :func:`_invalid_license_dict` (the admin-route payload shape)."""
    return base64.b64encode(
        json.dumps(_invalid_license_dict()).encode("utf-8")
    ).decode()


def test_failed_apply_preserves_a_working_license(licensing_state):
    """An invalid candidate must leave an already-valid license in place."""
    licensing.setup()
    applied = licensing.apply_test_license()
    assert applied["success"] is True
    assert licensing.is_free_tier() is False
    assert licensing.has_feature("plexijoin", default=False) is True

    result = licensing.apply_license_from_base64(_invalid_license_base64())

    assert result["success"] is False
    assert result.get("error")
    # The working license survived the failed apply (the regression).
    assert licensing.is_free_tier() is False
    assert licensing.is_valid() is True
    assert licensing.has_feature("plexijoin", default=False) is True


def test_failed_apply_leaves_free_tier_free(licensing_state):
    """A failed apply on a free-tier instance must not change anything either."""
    licensing.setup()
    # A clean, unlicensed manager: nothing valid is loaded yet.
    licensing._license_manager = licensing.LicenseManager()
    licensing._free_tier_mode = True
    assert licensing.is_free_tier() is True

    result = licensing.apply_license_from_base64(_invalid_license_base64())

    assert result["success"] is False
    assert licensing.is_free_tier() is True
    assert licensing.has_feature("plexijoin", default=False) is False


def test_failed_apply_from_dict_preserves_a_working_license(licensing_state):
    """The dict entry point is transactional too."""
    licensing.setup()
    assert licensing.apply_test_license()["success"] is True

    result = licensing.apply_license_from_dict(_invalid_license_dict())

    assert result["success"] is False
    assert licensing.is_free_tier() is False
    assert licensing.has_feature("plexijoin", default=False) is True
