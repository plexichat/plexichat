"""Verify-cache invalidation on auth-manager state mutations.

``AuthManager.verify_token`` memoises each token's ``TokenInfo`` for the
verify TTL. That object carries security-relevant state read from the DB:
``permissions``, ``account_locked`` and (for bots) the bot's very existence.
Any mutation of that state that skips the invalidation leaves a live session
or bot authenticating with the pre-change context until the cache expires.

These tests cover two such gaps that existed in the auth managers:
  * bot disable/delete/token-rotation/permission edits, and
  * account lockout reached through failed logins.
"""

import pytest
from unittest.mock import patch

from src.core.database import invalidate_pattern
from src.core.auth.exceptions import TokenInvalidError


def _make_bot(auth_manager, owner_id, name):
    return auth_manager.create_bot(owner_id, name, name + " Display")


def test_disabled_bot_token_not_served_from_stale_verify_cache(auth_manager, test_user):
    invalidate_pattern("token_verify:*")
    bot = _make_bot(auth_manager, test_user.id, "hardening_disabled_bot")
    token = bot.token

    # Warm the verify cache while the bot is enabled.
    assert auth_manager.verify_token(token).valid is True

    assert auth_manager.disable_bot(test_user.id, bot.id) is True

    # Without invalidation the cached (valid=True) TokenInfo keeps the disabled
    # bot authenticating for the rest of the verify TTL.
    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(token)


def test_regenerated_bot_token_revokes_cached_old_token(auth_manager, test_user):
    invalidate_pattern("token_verify:*")
    bot = _make_bot(auth_manager, test_user.id, "hardening_rotated_bot")
    old_token = bot.token

    assert auth_manager.verify_token(old_token).valid is True

    new_token = auth_manager.regenerate_bot_token(test_user.id, bot.id)
    assert new_token != old_token

    # The rotated-away credential must stop working immediately.
    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(old_token)
    assert auth_manager.verify_token(new_token).valid is True


def test_bot_permission_revocation_invalidates_verify_cache(auth_manager, test_user):
    invalidate_pattern("token_verify:*")
    bot = _make_bot(auth_manager, test_user.id, "hardening_perms_bot")
    token = bot.token

    warmed = auth_manager.verify_token(token)
    assert warmed.permissions

    auth_manager.update_bot_permissions(test_user.id, bot.id, {})

    # A cached TokenInfo would still carry the old (broader) permission map.
    assert auth_manager.verify_token(token).permissions == {}


def test_deleted_bot_stops_authenticating_immediately(auth_manager, test_user):
    invalidate_pattern("token_verify:*")
    bot = _make_bot(auth_manager, test_user.id, "hardening_deleted_bot")
    token = bot.token

    assert auth_manager.verify_token(token).valid is True
    assert auth_manager.delete_bot(test_user.id, bot.id) is True

    with pytest.raises(TokenInvalidError):
        auth_manager.verify_token(token)


def test_account_lockout_invalidates_verify_cache(auth_manager, test_user_with_token):
    """A distributed-lockout must suspend live sessions at once.

    The auth middleware trusts ``TokenInfo.account_locked``. If the lockout
    does not drop the verify cache, a session that already verified keeps
    reporting ``account_locked=False`` until the TTL and is never suspended.
    """
    invalidate_pattern("token_verify:*")
    manager = auth_manager._manager
    user_id = test_user_with_token["user"].id
    token = test_user_with_token["token"]

    # Warm the cache with the unlocked context.
    assert auth_manager.verify_token(token).account_locked is False

    # test config: max_failed_attempts=3, lockout_min_distinct_ips defaults to 2.
    manager._handle_failed_login(user_id, "203.0.113.10")
    manager._handle_failed_login(user_id, "203.0.113.11")
    manager._handle_failed_login(user_id, "203.0.113.12")

    locked = auth_manager.verify_token(token)
    assert locked.account_locked is True


# ---------------------------------------------------------------------------
# Contract coverage: every bot auth-state write must invalidate the cache.
# The enable path is included even though it only relaxes state, so the
# invalidation contract stays uniform across enable/disable.
# ---------------------------------------------------------------------------


class _FakeCursor:
    rowcount = 1


class _FakeDB:
    def fetch_one(self, *args, **kwargs):
        return {"owner_id": 1}

    def execute(self, *args, **kwargs):
        return _FakeCursor()


@pytest.mark.parametrize("method", ["enable_bot", "delete_bot"])
def test_bot_state_writes_invalidate_verify_cache(method):
    from src.core.auth.managers.bot import BotMixin

    mixin = BotMixin()
    mixin._db = _FakeDB()
    with patch("src.core.auth.managers.bot.invalidate_pattern") as invalidate:
        getattr(mixin, method)(1, 2)
    invalidate.assert_called_once_with("token_verify:*")
