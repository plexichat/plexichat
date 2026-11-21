"""An email change must retire trusted devices, not just sessions.

A device marked trusted skips the 2FA challenge at login with only the
password, so it is a persistent second-factor bypass. ``update_user`` already
revokes every session when the address changes (email is the recovery
identifier and password-reset destination), but it left the ``trusted`` flag on
``auth_devices`` intact. A stolen/lost device -- or anyone who ever learned a
previously trusted fingerprint -- therefore kept strolling past 2FA with only
the password after the victim moved the account to a new address. Password
change/reset and 2FA enable already retire trusted devices as part of the same
credential transition; an address change is the same class of transition and
must fail closed too.

Regression: changing the email must clear trust and restore the 2FA challenge,
while a non-credential profile edit (username only) must leave the trusted
device working.
"""

import uuid
from unittest.mock import patch

import pytest

from src.core.auth._lazy import _get_auth_manager
from src.utils import encryption


def _register_with_trusted_2fa_device(auth_manager, username, email, fingerprint):
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(username, email, "TestPass123!")
    with patch.object(encryption, "verify_password", return_value=True):
        # A successful password login creates the auth_devices row the trust
        # flag is attached to (trust is only granted to a previously seen
        # device).
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
class TestTrustedDeviceInvalidatedOnEmailChange:
    def test_email_change_clears_trust_and_restores_2fa(self, db, auth_manager):
        username = f"ec_trust_{uuid.uuid4().hex[:8]}"
        fp = f"fp_{uuid.uuid4().hex[:8]}"
        user = _register_with_trusted_2fa_device(
            auth_manager, username, f"{username}@test.local", fp
        )

        # Baseline: while the trusted device is live, password + fingerprint
        # skips the 2FA challenge (proves the fixture sets up a real bypass).
        with patch.object(encryption, "verify_password", return_value=True):
            before = auth_manager.login(
                username, "TestPass123!", device_info={"fingerprint": fp}
            )
        assert before.status.value == "success"

        new_email = f"moved_{uuid.uuid4().hex[:8]}@test.local"
        auth_manager.update_user(user.id, email=new_email)

        # A stale trusted fingerprint must NOT skip 2FA after the recovery
        # identifier moved to a new address.
        assert auth_manager.is_trusted_device(user.id, fp) is False
        with patch.object(encryption, "verify_password", return_value=True):
            after = auth_manager.login(
                username, "TestPass123!", device_info={"fingerprint": fp}
            )
        assert after.status.value == "two_factor_required"

    def test_username_only_change_keeps_trust(self, db, auth_manager):
        # Control: a non-credential profile edit must not retire trust, so the
        # fix is specific to an actual address change.
        username = f"ec_keep_{uuid.uuid4().hex[:8]}"
        fp = f"fp_{uuid.uuid4().hex[:8]}"
        user = _register_with_trusted_2fa_device(
            auth_manager, username, f"{username}@test.local", fp
        )

        new_username = f"renamed_{username}"
        auth_manager.update_user(user.id, username=new_username)

        assert auth_manager.is_trusted_device(user.id, fp) is True
        with patch.object(encryption, "verify_password", return_value=True):
            after = auth_manager.login(
                new_username, "TestPass123!", device_info={"fingerprint": fp}
            )
        assert after.status.value == "success"
