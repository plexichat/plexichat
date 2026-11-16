"""Deep hardening: bounded replay protection for refresh-token rotation.

Rotation used to invalidate the old token only by replacing its stored hash.
A stolen pre-rotation bearer was merely rejected on reuse; the session stayed
alive and the compromise went unnoticed. The bounded replay guard records
rotated tokens (digests only, with TTL + LRU eviction) so reuse is detected
and the session is revoked. This suite also pins the guard's memory bounds.
"""

from unittest.mock import patch

import pytest

from src.core.auth import tokens
from src.core.auth.exceptions import TokenInvalidError
from src.core.auth.replay import BoundedReplayGuard, refresh_replay_guard


@pytest.fixture(autouse=True)
def _clean_replay_guard():
    refresh_replay_guard.clear()
    yield
    refresh_replay_guard.clear()


def _login(auth_manager, username, password):
    with patch("src.utils.encryption.verify_password", return_value=True):
        result = auth_manager.login(username, password)
    return result


def test_guard_is_bounded_hashed_and_one_shot():
    guard = BoundedReplayGuard(max_entries=2, ttl_seconds=60)
    assert guard.consume("first") is True
    assert guard.consume("first") is False
    assert guard.contains("first") is True

    for i in range(10):
        guard.add(f"key-{i}")
    assert len(guard) <= 2
    # Only digests are retained, never the raw one-time values.
    assert all(len(k) == 64 and k != "key-0" for k in guard._entries)

    guard.clear()
    assert len(guard) == 0
    assert guard.contains("first") is False


def test_reusing_rotated_token_revokes_session(
    auth_manager, db, test_user_with_token
):
    result = _login(
        auth_manager,
        test_user_with_token["username"],
        test_user_with_token["password"],
    )
    old_token = result.token
    parsed = tokens.parse_token(old_token)
    assert parsed is not None

    new_token = auth_manager.refresh_session(old_token)
    assert new_token and new_token != old_token
    assert auth_manager.verify_token(new_token).valid is True

    # Replaying the pre-rotation bearer is detected and kills the session.
    assert auth_manager.refresh_session(old_token) is None
    with pytest.raises(Exception):
        auth_manager.verify_token(new_token)
    row = db.fetch_one(
        "SELECT revoked FROM auth_sessions WHERE id = ?", (parsed["id"],)
    )
    assert row is not None and row["revoked"] == 1


def test_refreshing_with_current_token_is_not_replay(
    auth_manager, test_user_with_token
):
    result = _login(
        auth_manager,
        test_user_with_token["username"],
        test_user_with_token["password"],
    )
    second = auth_manager.refresh_session(result.token)
    assert second
    third = auth_manager.refresh_session(second)
    assert third and third != second
    assert auth_manager.verify_token(third).valid is True


def test_replay_does_not_revoke_unrelated_session(auth_manager, test_user_with_token):
    first = _login(
        auth_manager,
        test_user_with_token["username"],
        test_user_with_token["password"],
    )
    other = _login(
        auth_manager,
        test_user_with_token["username"],
        test_user_with_token["password"],
    )
    rotated = auth_manager.refresh_session(first.token)
    assert rotated
    assert auth_manager.refresh_session(first.token) is None

    # The other independent session remains usable.
    with pytest.raises(Exception):
        auth_manager.verify_token(rotated)
    assert auth_manager.verify_token(other.token).valid is True


def test_malformed_token_still_denied(auth_manager):
    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token("1." + "a" * tokens.MIN_TOKEN_SECRET_LENGTH)
