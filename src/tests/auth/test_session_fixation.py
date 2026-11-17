"""Session-fixation regression: login always mints a fresh session id.

A pre-login (attacker-chosen or otherwise known) session must never become the
authenticated session.  The core login path creates a brand-new session row and
token, so the previous session id cannot be reused as the credential handed
back to the client.
"""

from unittest.mock import patch

import pytest

from src.core.auth.exceptions import TokenInvalidError


def _session_id(token: str) -> int:
    """Session id is the first component of a session token."""
    return int(token.split(".", 1)[0])


def _register_and_login(auth_manager, username):
    from src.utils import encryption

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username,
            email=f"{username}@example.com",
            password="TestPass123!",
        )
    return user


class TestSessionFixation:
    def test_login_rotates_session_id(self, db, auth_manager):
        _register_and_login(auth_manager, "fixation_rotate_user")
        from src.utils import encryption

        with patch.object(encryption, "verify_password", return_value=True):
            pre_login = auth_manager.login("fixation_rotate_user", "TestPass123!")
            post_login = auth_manager.login("fixation_rotate_user", "TestPass123!")

        assert post_login.token != pre_login.token
        assert _session_id(post_login.token) != _session_id(pre_login.token)

        # Each token resolves to its own session id — the login did not adopt
        # the session that existed before it.
        assert auth_manager.verify_token(pre_login.token).session_id == _session_id(
            pre_login.token
        )
        assert auth_manager.verify_token(post_login.token).session_id == _session_id(
            post_login.token
        )

    def test_pre_login_session_cannot_be_reused_after_login(self, db, auth_manager):
        user = _register_and_login(auth_manager, "fixation_reuse_user")
        from src.utils import encryption

        with patch.object(encryption, "verify_password", return_value=True):
            pre_login = auth_manager.login("fixation_reuse_user", "TestPass123!")
            post_login = auth_manager.login("fixation_reuse_user", "TestPass123!")

        pre_sid = _session_id(pre_login.token)
        post_sid = _session_id(post_login.token)

        # Revoking the pre-login session kills it but leaves the fresh one
        # untouched: they are genuinely distinct sessions, not one id reused.
        assert auth_manager.revoke_session(user.id, pre_sid) is True
        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token(pre_login.token)
        assert auth_manager.verify_token(post_login.token).valid is True
        assert auth_manager.verify_token(post_login.token).session_id == post_sid
