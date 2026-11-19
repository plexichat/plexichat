"""Regression: ``logout_all`` must verify the ``except_token`` credential.

``except_token`` decides which session survives a "log out everywhere else"
mass revocation. It is a credential, not an id hint: only a token whose secret
matches a live session owned by the caller may keep that session alive. The
previous implementation trusted the *unverified* id parsed from the token, so
forging ``<known session id>.<arbitrary secret>`` kept that session alive
while every other session was revoked.
"""

from unittest.mock import patch

import pytest

from src.core.auth.exceptions import TokenInvalidError
from src.core.auth.tokens import parse_token


def _register_and_login(auth_manager, username):
    with patch(
        "src.utils.encryption.hash_password", return_value="fake_hash_$test"
    ):
        user = auth_manager.register(
            username=username,
            email=f"{username}@example.com",
            password="TestPass123!",
        )
    with patch("src.utils.encryption.verify_password", return_value=True):
        sessions = [
            auth_manager.login(username, "TestPass123!") for _ in range(3)
        ]
    return user, sessions


def _forged_token_for_session(valid_token, forged_secret="A" * 43):
    """Build ``<same id>.<wrong secret>`` — a plausible but invalid token."""
    parsed = parse_token(valid_token)
    assert parsed is not None and parsed["token_type"] == "session"
    return f"{parsed['id']}.{forged_secret}"


def test_forged_except_token_cannot_keep_a_session_alive(auth_manager):
    """A forged except_token must fail closed and revoke every session."""
    user, sessions = _register_and_login(auth_manager, "logoutall_forged_except")
    keep = sessions[2]

    forged = _forged_token_for_session(keep.token)
    assert forged != keep.token
    auth_manager.logout_all(user.id, except_token=forged)

    # The forged token did not carry the real secret, so the session it named
    # must be revoked just like all the others.
    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(keep.token)
    for s in sessions[:2]:
        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token(s.token)


def test_other_users_except_token_cannot_keep_a_session_alive(auth_manager):
    """A valid token naming another user's session must not be honoured."""
    user_a, sessions_a = _register_and_login(auth_manager, "logoutall_except_user_a")
    user_b, sessions_b = _register_and_login(auth_manager, "logoutall_except_user_b")

    # user_b's genuine token used as user_a's "keep" token: the id belongs to
    # a session owned by user_b, so it must not exempt any of user_a's rows.
    auth_manager.logout_all(user_a.id, except_token=sessions_b[0].token)

    for s in sessions_a:
        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token(s.token)
    # user_b's session was not touched by user_a's mass revocation.
    assert auth_manager.verify_token(sessions_b[0].token).valid is True


def test_valid_except_token_still_keeps_session(auth_manager):
    """The legitimate 'keep current session' behaviour is unchanged."""
    user, sessions = _register_and_login(auth_manager, "logoutall_valid_except")
    keep = sessions[2]

    auth_manager.logout_all(user.id, except_token=keep.token)

    assert auth_manager.verify_token(keep.token).valid is True
    for s in sessions[:2]:
        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token(s.token)
