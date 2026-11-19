"""Regression: a license that lapses mid-process must stop granting entitlements.

``LicenseManager`` caches its validation verdict. Expiry is time-dependent, so a
cached "valid" verdict must be re-evaluated once the wall clock passes
``expires_at``. Otherwise a long-running server that started with a valid
license keeps premium features, tiered configs and resource limits forever
after the license has expired -- effectively ignoring the expiry date.
"""

import base64
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.licensing import core as licensing_core
from utils.licensing.core import LicenseManager, generate_keypair, sign_license


def _signed_license(expires_at, features=None, limits=None):
    """Build a properly Ed25519-signed license expiring at ``expires_at``."""
    private_key, public_key = generate_keypair()
    now = int(time.time())
    data = {
        "version": "1.0",
        "instance_id": "expiry-repro",
        "issued_at": now - 100,
        "expires_at": expires_at,
        "features": (
            {"plexijoin": True, "bond": True} if features is None else features
        ),
        "limits": ({"max_users": 100} if limits is None else limits),
    }
    data["signature"] = sign_license(private_key, data)
    return data, public_key


def test_expiry_revokes_all_entitlement_getters():
    """No reload / re-apply: the cached verdict must expire with the clock."""
    now = int(time.time())
    data, public_key = _signed_license(expires_at=now + 10)
    manager = LicenseManager(public_key_bytes=public_key)
    manager.load_from_dict(data)

    with patch.object(licensing_core.time, "time", return_value=now):
        assert manager.is_valid() is True
        assert manager.has_feature("plexijoin", default=False) is True
        assert manager.has_feature("bond", default=False) is True
        assert manager.get_feature_config("bond") is True
        assert manager.get_feature_limit("max_users", -1) == 100
        assert manager.get_instance_id() == "expiry-repro"

    # The wall clock crosses expires_at while the process stays up.
    with patch.object(licensing_core.time, "time", return_value=now + 60):
        assert manager.is_valid() is False
        assert manager.has_feature("plexijoin", default=False) is False
        assert manager.has_feature("bond", default=False) is False
        assert manager.get_feature_config("bond") is None
        assert manager.get_feature_limit("max_users", -1) == -1
        assert manager.get_instance_id() is None
        assert manager.validate().is_expired is True


def test_perpetual_license_is_not_periodically_revalidated():
    """A license without an expiry must keep working indefinitely."""
    data, public_key = _signed_license(expires_at=None)
    manager = LicenseManager(public_key_bytes=public_key)
    manager.load_from_dict(data)

    start = int(time.time())
    with patch.object(licensing_core.time, "time", return_value=start):
        assert manager.is_valid() is True
    with patch.object(licensing_core.time, "time", return_value=start + 10**8):
        assert manager.is_valid() is True
        assert manager.has_feature("plexijoin", default=False) is True


def test_expired_license_is_not_signature_reverified_on_every_check():
    """Revalidation happens once; the new invalid verdict is then cached."""
    now = int(time.time())
    data, public_key = _signed_license(expires_at=now + 10)
    manager = LicenseManager(public_key_bytes=public_key)
    manager.load_from_dict(data)

    with patch.object(licensing_core.time, "time", return_value=now):
        assert manager.is_valid() is True

    with patch.object(licensing_core.time, "time", return_value=now + 60):
        real_verify = manager._verify_signature
        calls = []

        def counting_verify():
            calls.append(1)
            return real_verify()

        with patch.object(manager, "_verify_signature", side_effect=counting_verify):
            for _ in range(5):
                assert manager.is_valid() is False
            assert len(calls) == 1


@pytest.fixture
def licensing_state():
    """Restore the process-wide licensing module globals after each test."""
    import utils.licensing as licensing

    keys = (
        "_license_manager",
        "_free_tier_mode",
        "_setup_called",
        "_PLEXICHAT_PUBLIC_KEY_BASE64",
    )
    saved = tuple(getattr(licensing, key) for key in keys)
    saved_class_key = licensing.LicenseManager._PUBLIC_KEY_BASE64
    try:
        yield licensing
    finally:
        for key, value in zip(keys, saved):
            setattr(licensing, key, value)
        licensing.LicenseManager._PUBLIC_KEY_BASE64 = saved_class_key


def test_module_level_entitlements_revoked_after_expiry(
    licensing_state, tmp_path, monkeypatch
):
    """The public ``utils.licensing`` API reflects an expired license too."""
    licensing = licensing_state
    now = int(time.time())
    data, public_key = _signed_license(
        expires_at=now + 10, features={"plexijoin": True}
    )
    public_b64 = base64.b64encode(public_key).decode()

    monkeypatch.delenv("PLEXICHAT_LICENSE", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    license_file = tmp_path / "license.json"
    license_file.write_text(json.dumps(data))

    with patch.object(licensing_core.time, "time", return_value=now):
        assert (
            licensing.setup(
                public_key_base64=public_b64, license_path=str(license_file)
            )
            is True
        )
        assert licensing.is_valid() is True
        assert licensing.has_feature("plexijoin", default=False) is True

    with patch.object(licensing_core.time, "time", return_value=now + 60):
        assert licensing.is_valid() is False
        assert licensing.has_feature("plexijoin", default=False) is False
        verdict = licensing.get_validation_result()
        assert verdict is not None and verdict.is_expired is True
