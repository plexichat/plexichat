"""Enabling 2FA must also retire trusted devices, not just sessions.

A device marked trusted skips the 2FA challenge at login with only the
password, so it is a persistent second-factor bypass. ``confirm_2fa`` revokes
every pre-2FA session, but if the ``trusted`` flag on ``auth_devices`` survives
the switch, a stolen/lost device (or anyone who ever learned a trusted
fingerprint) keeps strolling past the freshly enabled second factor. Enabling
2FA is a credential transition exactly like a password rotation, which already
retires trusted devices; leaving them live means the new factor protects
nothing against a trusted device.

Regression: a device trusted before 2FA is enabled must not skip the challenge
afterwards, while an account with no trusted device still requires 2FA.
"""

import uuid
from unittest.mock import patch

import pyotp
import pytest

from src.utils import encryption


def _register_and_login(auth, username, fingerprint):
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth.register(username, f"{username}@example.com", "TestPass123!")
    with patch.object(encryption, "verify_password", return_value=True):
        # A successful password login creates the auth_devices row the trust
        # flag is attached to (trust is only granted to previously seen devices).
        auth.login(username, "TestPass123!", device_info={"fingerprint": fingerprint})
    return user


@pytest.mark.auth
class TestTwoFactorEnableRevokesTrustedDevices:
    def test_enabling_2fa_clears_preexisting_trusted_devices(self, db, auth_manager):
        username = f"twofa_trust_{uuid.uuid4().hex[:8]}"
        fingerprint = f"fp_{uuid.uuid4().hex[:8]}"
        user = _register_and_login(auth_manager, username, fingerprint)

        # Precondition: the device is known and explicitly trusted while the
        # account still has no second factor.
        assert auth_manager.trust_device(user.id, fingerprint) is True
        assert auth_manager.is_trusted_device(user.id, fingerprint) is True

        setup = auth_manager.setup_2fa(user.id)
        assert auth_manager.confirm_2fa(user.id, pyotp.TOTP(setup.secret).now()) is True

        # The trusted device was established before the second factor existed;
        # it must not become a permanent 2FA bypass once 2FA is switched on.
        assert auth_manager.is_trusted_device(user.id, fingerprint) is False
        with patch.object(encryption, "verify_password", return_value=True):
            after = auth_manager.login(
                username, "TestPass123!", device_info={"fingerprint": fingerprint}
            )
        assert after.status.value == "two_factor_required"

    def test_enabling_2fa_without_trusted_device_still_challenges(
        self, db, auth_manager
    ):
        # Control: the fix must not depend on a trusted device existing. A
        # plain password-only login after enabling 2FA is always challenged.
        username = f"twofa_notrust_{uuid.uuid4().hex[:8]}"
        fingerprint = f"fp_{uuid.uuid4().hex[:8]}"
        user = _register_and_login(auth_manager, username, fingerprint)

        setup = auth_manager.setup_2fa(user.id)
        assert auth_manager.confirm_2fa(user.id, pyotp.TOTP(setup.secret).now()) is True

        with patch.object(encryption, "verify_password", return_value=True):
            after = auth_manager.login(
                username, "TestPass123!", device_info={"fingerprint": fingerprint}
            )
        assert after.status.value == "two_factor_required"
