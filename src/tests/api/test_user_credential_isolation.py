"""Regression tests for credential isolation in the users router.

Two related weaknesses are covered:

1. ``generate_cache_key`` reduces ``TokenInfo`` to ``TokenInfo:<user_id>``.
   Bot tokens carry their *owner's* ``user_id`` (the bot id lives in
   ``account_id``), so a bot token and its owner's user session shared every
   ``@cached`` key. Private responses -- own profile, DM list, messaging
   settings -- cached by one credential were served to the other.

2. Every ``/@me`` handler except ``GET /@me`` keyed off ``user_id``, which for
   a bot token is the owner. A bot credential could therefore read or mutate
   the owner's account (profile, DMs, settings, deletion, data exports).
"""

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.middleware.authentication import TokenInfo
from src.api.routes.users.composer import UsersRouter
from src.api.routes.users.helpers import authenticated_cache_key
from src.api.schemas.common import SnowflakeID
from src.api.schemas.users import UserDeletionRequest, UserUpdateRequest
from src.core.auth.models import AccountType
from src.core.database.cache.serialization import generate_cache_key


def _token(user_id, *, token_type="user", account_id=None, session_id=None):
    return TokenInfo(
        valid=True,
        token_type=token_type,
        account_id=SnowflakeID(str(account_id if account_id is not None else user_id)),
        user_id=SnowflakeID(str(user_id)),
        session_id=SnowflakeID(str(session_id)) if session_id is not None else None,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="tester",
        account_type=AccountType.BOT if token_type == "bot" else AccountType.USER,
    )


def _call(method, **kwargs):
    """Invoke a route handler, driving coroutine handlers to completion."""
    result = method(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def _owner_and_bot():
    return _token(100, session_id=1), _token(100, token_type="bot", account_id=500)


def test_cache_key_partitions_bot_credential_from_owner_session():
    owner, bot = _owner_and_bot()
    other_bot = _token(100, token_type="bot", account_id=501)
    builder = authenticated_cache_key("current_user_api")

    owner_key = builder(current_user=owner)
    bot_key = builder(current_user=bot)

    # User tokens keep the legacy layout (so existing invalidation matches).
    assert owner_key == generate_cache_key("current_user_api", current_user=owner)
    # A bot token can never land on its owner's (or another bot's) key.
    assert bot_key != owner_key
    assert ":bot:500:" in bot_key
    assert bot_key != builder(current_user=other_bot)


def test_bot_token_cannot_read_owner_cached_private_profile():
    owner, bot = _owner_and_bot()

    human = SimpleNamespace(
        id=100,
        username="owner",
        email="owner@example.com",
        avatar_url=None,
        created_at=1,
        email_verified=True,
        totp_enabled=True,
        age_verified=True,
        badges=[],
        deletion_status="active",
        deletion_at=None,
    )
    bot_obj = SimpleNamespace(id=500, username="owner-bot", created_at=2)

    auth = MagicMock()
    auth.get_user.return_value = human
    auth.get_bot.return_value = bot_obj

    router = UsersRouter()
    with patch("src.api.routes.users.profile.api.get_auth", return_value=auth):
        owner_resp = router.get_current_user_info(current_user=owner)
        bot_resp = router.get_current_user_info(current_user=bot)

    assert owner_resp.email == "owner@example.com"  # owner sees private fields
    assert bot_resp.email is None  # bot gets its own synthetic profile
    assert str(bot_resp.id) == "500"
    # The bot branch actually executed instead of replaying the cached owner
    # response -- the proof the cache entries are partitioned.
    auth.get_bot.assert_called_once()


@pytest.mark.parametrize(
    "method_name, call_kwargs",
    [
        (
            "update_current_user",
            {"body": UserUpdateRequest(username="renamed")},
        ),
        (
            "schedule_account_deletion",
            {"body": UserDeletionRequest(password="pw")},
        ),
        ("get_notes_channel", {}),
        ("get_dm_channels", {}),
        ("get_messaging_settings", {}),
        ("get_data_export_requests", {}),
    ],
)
def test_bot_token_rejected_on_owner_private_endpoints(method_name, call_kwargs):
    _, bot = _owner_and_bot()
    router = UsersRouter()

    with pytest.raises(HTTPException) as exc:
        _call(getattr(router, method_name), current_user=bot, **call_kwargs)

    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403


def test_user_token_still_allowed_for_owner_private_endpoints():
    user, _ = _owner_and_bot()
    messaging = MagicMock()
    messaging.get_dm_channels.return_value = []

    with patch(
        "src.api.routes.users.channel.api.get_messaging", return_value=messaging
    ):
        result = _call(UsersRouter().get_dm_channels, current_user=user)

    assert result == []
    messaging.get_dm_channels.assert_called_once_with(SnowflakeID("100"))
