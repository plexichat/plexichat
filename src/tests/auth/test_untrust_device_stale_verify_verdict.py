"""Untrusting a device must retire the 2FA-skip session and its warm verdict.

A trusted device is a persistent second-factor bypass: a password login from
that fingerprint skips the TOTP challenge entirely and mints a full session.
Removing that trust (a user untrusting a lost/stolen device) is a second-factor
removal exactly like password change/reset, email change, 2FA enable/disable,
passkey revocation and device deletion -- every one of which also revokes the
sessions authenticated with that credential and drops the warm
``token_verify:*`` verdict.

Without this, a bearer token obtained by skipping 2FA on the now-untrusted
device keeps authenticating for the rest of its lifetime (and for the whole
verify-cache TTL even after a session revoke), so the "removed" second factor
is still effectively available to whoever holds that token.

Regression: after ``untrust_device`` a 2FA-skip session from that device must
fail closed, including when its verify verdict was warmed before the call.
"""

import uuid
from unittest.mock import patch

import pyotp
import pytest

from src.core.auth.exceptions import TokenInvalidError
from src.utils import encryption

pytestmark = [pytest.mark.auth, pytest.mark.unit]


def _register_with_2fa_and_trusted_device(auth, username, fingerprint):
    """Register, enable TOTP, trust a device, and login skipping 2FA."""
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth.register(username, f"{username}@example.com", "TestPass123!")

    setup = auth.setup_2fa(user.id)
    assert auth.confirm_2fa(user.id, pyotp.TOTP(setup.secret).now()) is True

    # The password login creates the auth_devices row trust attaches to, but
    # TOTP is now on so it returns a challenge rather than a session.
    with patch.object(encryption, "verify_password", return_value=True):
        first = auth.login(
            username, "TestPass123!", device_info={"fingerprint": fingerprint}
        )
    assert first.status.value == "two_factor_required"

    assert auth.trust_device(user.id, fingerprint) is True
    assert auth.is_trusted_device(user.id, fingerprint) is True

    # A password login from the trusted fingerprint skips 2FA and mints a
    # fully-privileged session.
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth.login(
            username, "TestPass123!", device_info={"fingerprint": fingerprint}
        )
    assert result.status.value == "success"
    assert result.token
    return user, result.token


def test_untrust_revokes_2fa_skip_session_and_warm_verdict(db, auth_manager):
    username = f"untrust_verdict_{uuid.uuid4().hex[:8]}"
    fingerprint = f"fp_{uuid.uuid4().hex[:8]}"
    user, token = _register_with_2fa_and_trusted_device(
        auth_manager, username, fingerprint
    )

    # Warm the token_verify cache exactly as any authenticated request would.
    # A session revoke that forgets to drop this entry leaves a stale verdict.
    assert auth_manager.verify_token(token).valid is True

    assert auth_manager.untrust_device(user.id, fingerprint) is True
    assert auth_manager.is_trusted_device(user.id, fingerprint) is False

    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(token)


def test_failed_untrust_keeps_session(db, auth_manager):
    """Control: a refused untrust (unknown fingerprint) has no side effect."""
    username = f"untrust_noop_{uuid.uuid4().hex[:8]}"
    fingerprint = f"fp_{uuid.uuid4().hex[:8]}"
    user, token = _register_with_2fa_and_trusted_device(
        auth_manager, username, fingerprint
    )

    assert auth_manager.untrust_device(user.id, "never-seen-fingerprint") is False
    assert auth_manager.verify_token(token).valid is True
