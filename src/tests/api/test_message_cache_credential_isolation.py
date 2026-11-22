"""Regression: cached message reads must not share a bot's key with its owner.

``generate_cache_key`` reduces a ``TokenInfo`` to ``TokenInfo:<user_id>``.
Bot tokens carry their *owner's* ``user_id`` (the bot id lives in
``account_id``), so a bot credential and the owner's user session collide on
every ``@cached`` key that uses the default builder. Message list, pinned and
search responses contain private channel content, so a response produced under
one credential could be replayed to the other.

These routes must use ``authenticated_cache_key`` as their key builder so bot
credentials get a separate namespace while user-session keys keep their legacy
shape (so existing invalidation patterns still match).
"""

import pytest

from src.api.middleware.authentication import TokenInfo
from src.api.routes.messages import messages_list, search
from src.api.schemas.common import SnowflakeID
from src.core.auth.models import AccountType
from src.core.database.cache.serialization import generate_cache_key


def _token(user_id, *, token_type="user", account_id=None):
    return TokenInfo(
        valid=True,
        token_type=token_type,
        account_id=SnowflakeID(str(account_id if account_id is not None else user_id)),
        user_id=SnowflakeID(str(user_id)),
        session_id=None,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="tester",
        account_type=AccountType.BOT if token_type == "bot" else AccountType.USER,
    )


def _key_builder(func):
    """Return the ``key_builder`` captured by the ``@cached`` wrapper."""
    for name, cell in zip(func.__code__.co_freevars, func.__closure__ or ()):
        if name == "key_builder":
            return cell.cell_contents
    return None


@pytest.mark.parametrize(
    "func, prefix",
    [
        (messages_list.get_channel_messages, "messages_api"),
        (messages_list.get_pinned_messages, "channel_pins_api"),
        (search._search_messages_cached, "search_messages_api"),
    ],
)
def test_private_message_cache_partitions_bot_credential_from_owner(func, prefix):
    owner = _token(100)
    bot = _token(100, token_type="bot", account_id=500)

    builder = _key_builder(func)
    assert builder is not None, (
        f"{func.__name__} still uses the default cache key builder; "
        "a bot token would share its owner's cached private messages"
    )

    call_kwargs = dict(channel_id="100", limit=50, current_user=owner)
    owner_key = builder(**call_kwargs)
    bot_key = builder(**dict(call_kwargs, current_user=bot))

    # User sessions keep the legacy key layout (invalidation still matches).
    assert owner_key == generate_cache_key(prefix, **call_kwargs)
    # The bot can never land on its owner's entry or replay it.
    assert bot_key != owner_key
    assert ":bot:500:" in bot_key
