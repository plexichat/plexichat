"""Enabling 2FA must retire sessions minted before the second factor existed.

A session created by a password-only login stays a fully credentialed bearer
after ``confirm_2fa`` flips ``totp_enabled``. If it survives, enabling 2FA does
not actually protect the account: a token stolen before the switch keeps working
indefinitely. The disable path already revokes sessions; enabling is the same
credential transition and must do the same.

The current session (the one that just proved possession of the TOTP secret by
confirming setup) may be preserved via ``except_session_id``; every other
password-only session must be revoked, and with no exception id *all* of them
must be.
"""

from unittest.mock import patch

import pyotp
import pytest

from src.core.auth.exceptions import TokenInvalidError
from src.utils import encryption


def _register(auth, username):
    return auth.register(
        username=username,
        email=f"{username}@example.com",
        password="TestPass123!",
    )


def _login_twice(auth, username):
    with patch.object(encryption, "verify_password", return_value=True):
        current = auth.login(username, "TestPass123!")
        other = auth.login(username, "TestPass123!")
    return current, other


def _enable_2fa(auth, user_id):
    setup = auth.setup_2fa(user_id)
    return setup.secret


def test_confirm_2fa_revokes_all_preexisting_sessions_without_exception(
    auth_manager, db
):
    user = _register(auth_manager, "enable_2fa_revoke_all")
    current, other = _login_twice(auth_manager, "enable_2fa_revoke_all")
    secret = _enable_2fa(auth_manager, user.id)

    assert auth_manager.confirm_2fa(user.id, pyotp.TOTP(secret).now()) is True

    # Both sessions predate 2FA and neither was re-challenged: both must die.
    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(current.token)
    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(other.token)


def test_confirm_2fa_preserves_only_the_confirming_session(auth_manager, db):
    user = _register(auth_manager, "enable_2fa_revoke_others")
    current, other = _login_twice(auth_manager, "enable_2fa_revoke_others")
    current_sid = int(current.token.split(".", 1)[0])
    secret = _enable_2fa(auth_manager, user.id)

    assert (
        auth_manager.confirm_2fa(
            user.id, pyotp.TOTP(secret).now(), except_session_id=current_sid
        )
        is True
    )

    info = auth_manager.verify_token(current.token)
    assert info.valid is True
    assert info.session_id == current_sid
    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(other.token)
