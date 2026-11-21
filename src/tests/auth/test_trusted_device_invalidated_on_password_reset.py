"""Regression: trusted devices (which skip 2FA) must die with a credential
rotation.

A device marked trusted bypasses the 2FA challenge at login with only the
password. Password change/reset revoked every session but left the ``trusted``
flag on ``auth_devices`` intact, so a stolen/lost device -- or anyone who ever
learned a previously trusted fingerprint -- kept a permanent 2FA bypass after
the victim rotated their password to recover the account. This is exactly the
"stale trusted device" case that must fail closed.
"""

import re
import uuid
from unittest.mock import patch

import pytest

from src.core.auth._lazy import _get_auth_manager
from src.utils import encryption


_TOKEN_RE = re.compile(r"Token \(fallback\):\s*(\S+)")


class _CapturingSender:
    def __init__(self):
        self.bodies = []

    def send(self, email, subject, body):
        self.bodies.append(body)
        return True

    def tokens(self):
        return [
            m.group(1) for body in self.bodies for m in [_TOKEN_RE.search(body)] if m
        ]


def _register_with_trusted_2fa_device(auth_manager, username, fingerprint):
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username, f"{username}@example.com", "TestPass123!"
        )
    with patch.object(encryption, "verify_password", return_value=True):
        auth_manager.login(
            username, "TestPass123!", device_info={"fingerprint": fingerprint}
        )

    # Mirror confirm_2fa: the second factor is now on, and the device below was
    # explicitly remembered so future logins skip the challenge.
    _get_auth_manager().get_instance()._db.execute(
        "UPDATE auth_users SET totp_enabled = 1, totp_secret_encrypted = ? WHERE id = ?",
        ("x" * 32, user.id),
    )
    assert auth_manager.trust_device(user.id, fingerprint) is True
    return user


@pytest.mark.auth
class TestTrustedDeviceInvalidatedOnCredentialRotation:
    def test_change_password_clears_trust_and_restores_2fa(self, db, auth_manager):
        username = f"rot_cp_{uuid.uuid4().hex[:8]}"
        fp = "changepassworddevicefp"
        user = _register_with_trusted_2fa_device(auth_manager, username, fp)

        # Baseline: while the password is current, the remembered device skips
        # 2FA (proves the fixture actually sets up a working bypass).
        with patch.object(encryption, "verify_password", return_value=True):
            before = auth_manager.login(
                username, "TestPass123!", device_info={"fingerprint": fp}
            )
        assert before.status.value == "success"

        with patch.object(encryption, "verify_password", return_value=True):
            assert (
                auth_manager.change_password(user.id, "TestPass123!", "NewPassword456!")
                is True
            )

        # A stale trusted fingerprint must NOT skip 2FA after the rotation.
        assert auth_manager.is_trusted_device(user.id, fp) is False
        with patch.object(encryption, "verify_password", return_value=True):
            after = auth_manager.login(
                username, "NewPassword456!", device_info={"fingerprint": fp}
            )
        assert after.status.value == "two_factor_required"

    def test_password_reset_clears_trust_and_restores_2fa(self, db, auth_manager):
        username = f"rot_pr_{uuid.uuid4().hex[:8]}"
        fp = "resetpassworddevicefp"
        user = _register_with_trusted_2fa_device(auth_manager, username, fp)

        sender = _CapturingSender()
        _get_auth_manager().get_instance().email_sender = sender
        assert auth_manager.request_password_reset(f"{username}@example.com") is True
        (token,) = sender.tokens()
        assert auth_manager.reset_password(token, "BrandNewPassword789!") is True

        assert auth_manager.is_trusted_device(user.id, fp) is False
        with patch.object(encryption, "verify_password", return_value=True):
            after = auth_manager.login(
                username, "BrandNewPassword789!", device_info={"fingerprint": fp}
            )
        assert after.status.value == "two_factor_required"
