"""Bounded, per-credential caching for the hot reaction-list read.

``GET /channels/{id}/messages/{id}/reactions`` used to hydrate the reaction
rows, aggregate counts and the per-viewer ``me`` flag, then serialize the whole
list on every message view. These tests pin the new contract:

* a warm read is byte-for-byte identical to a cold one but performs no
  aggregation query (query reduction);
* the cache key is partitioned per credential, so two users -- and a bot vs.
  its owner -- never share an entry (no cross-user / bot-owner leak);
* the message-access gate runs on *every* request before the cache lookup, so
  denials are never cached and a revoked membership is not served a warm body;
* every reaction mutation drops the namespace, so a cached pill list cannot
  survive a write.

The checks themselves are not weakened: the real route functions, key builder
and cache decorator stay on the path; only the core module is replaced by a
counting fake.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes import reactions as reactions_routes
from src.api.routes.reactions import (
    _REACTION_READ_PREFIX,
    add_reaction,
    clear_reactions,
    clear_reactions_for_emoji,
    get_reactions,
    remove_reaction,
)
from src.core.auth.models import AccountType, TokenInfo
from src.core.database.cache.operations import invalidate_pattern
from src.core.reactions.models import MessageReactions, ReactionCount

pytestmark = [pytest.mark.unit]

MSG_ID = 5001
CHANNEL_ID = 42
EMOJI = "👍"


def _token(uid: int, token_type: str = "user", account_id: int | None = None):
    return TokenInfo(
        valid=True,
        token_type=token_type,
        account_id=account_id if account_id is not None else uid,
        user_id=uid,
        session_id=None,
        permissions={},
        rate_limit_tier="default",
        expires_at=None,
        username=f"user-{uid}",
        account_type=AccountType.USER,
    )


class _RateLimitResult:
    allowed = True
    retry_after = 0


class _FakeReactions:
    """Counting stand-in for the core reactions module."""

    def __init__(self):
        self.allowed = True
        self.access_calls: list[tuple[int, int]] = []
        self.detail_calls: list[tuple[int, int]] = []
        self.mutations: list[str] = []
        self.conversation_lookups: list[int] = []
        # Per-viewer pill data: ``me`` and even the count are viewer-specific
        # (blocked-user filtering), so a cross-credential hit is observable.
        self.pills: dict[int, list[tuple[str, int, bool]]] = {}

    def _pills_for(self, user_id: int) -> list[tuple[str, int, bool]]:
        return self.pills.get(user_id, [(EMOJI, 3, user_id == 42)])

    def can_access_message(self, user_id: int, message_id: int) -> bool:
        self.access_calls.append((user_id, message_id))
        return self.allowed

    def get_reactions(self, user_id: int, message_id: int) -> MessageReactions:
        self.detail_calls.append((user_id, message_id))
        return MessageReactions(
            message_id=message_id,
            reactions=[
                ReactionCount(
                    message_id=message_id,
                    emoji=emoji,
                    count=count,
                    me=me,
                )
                for emoji, count, me in self._pills_for(user_id)
            ],
        )

    def resolve_message_channel(self, message_id: int):
        return {"channel_id": CHANNEL_ID}

    def get_conversation_id_from_message(self, message_id: int):
        # The write routes run the channel-view gate before mutating. No server
        # channel backs this message, so the gate defers to the manager.
        self.conversation_lookups.append(message_id)
        return None

    def add_reaction(self, user_id, message_id, emoji):
        self.mutations.append("add")

    def remove_reaction(self, user_id, message_id, emoji):
        self.mutations.append("remove")

    def remove_all_reactions(self, user_id, message_id):
        self.mutations.append("clear_all")

    def remove_all_reactions_for_emoji(self, user_id, message_id, emoji):
        self.mutations.append("clear_emoji")


@pytest.fixture(autouse=True)
def _clean_reaction_read_cache():
    invalidate_pattern(f"{_REACTION_READ_PREFIX}:*")
    yield
    invalidate_pattern(f"{_REACTION_READ_PREFIX}:*")


@pytest.fixture
def fake_reactions():
    fake = _FakeReactions()
    with patch.object(api, "get_reactions", return_value=fake):
        yield fake


@pytest.fixture
def mutation_env():
    """Stub the rate limiter, server lookup and fire-and-forget dispatch so
    the real mutation route bodies can run without a live app/loop."""
    # The write routes fail closed when the servers module is unwired; wire a
    # store with no server-channel alias so the view gate passes and the cache
    # invalidation under test is actually reached.
    class _Servers:
        def get_channel_by_conversation(self, conversation_id):
            return None

    with (
        patch.object(
            reactions_routes.ratelimit,
            "acheck_rate_limit",
            side_effect=lambda **kwargs: _RateLimitResult(),
        ),
        patch.object(api, "get_servers", return_value=_Servers()),
        patch.object(
            reactions_routes.asyncio,
            "create_task",
            side_effect=lambda coro: coro.close(),
        ),
    ):
        yield SimpleNamespace(state=SimpleNamespace(is_internal=False))


@pytest.mark.asyncio
async def test_hit_is_identical_and_reduces_queries(fake_reactions):
    token = _token(42)
    first = await get_reactions(str(CHANNEL_ID), str(MSG_ID), current_user=token)
    second = await get_reactions(str(CHANNEL_ID), str(MSG_ID), current_user=token)

    assert [r.model_dump() for r in first] == [r.model_dump() for r in second]
    # One hydration across two reads: the second was served from cache.
    assert len(fake_reactions.detail_calls) == 1
    # The authz gate still ran on both requests.
    assert fake_reactions.access_calls == [(42, MSG_ID), (42, MSG_ID)]


@pytest.mark.asyncio
async def test_per_user_keys_do_not_leak(fake_reactions):
    alice, bob = _token(42), _token(99)
    fake_reactions.pills[42] = [(EMOJI, 5, True)]
    fake_reactions.pills[99] = [(EMOJI, 1, False)]

    alice_pills = await get_reactions(str(CHANNEL_ID), str(MSG_ID), current_user=alice)
    bob_pills = await get_reactions(str(CHANNEL_ID), str(MSG_ID), current_user=bob)

    assert [r.model_dump() for r in alice_pills] == [
        {
            "emoji": EMOJI,
            "count": 5,
            "me": True,
            "url": None,
            "is_custom": False,
            "custom_emoji_id": None,
        }
    ]
    assert [r.model_dump() for r in bob_pills] == [
        {
            "emoji": EMOJI,
            "count": 1,
            "me": False,
            "url": None,
            "is_custom": False,
            "custom_emoji_id": None,
        }
    ]
    assert len(fake_reactions.detail_calls) == 2  # distinct per-user keys

    # Repeating each read now hits its own entry; neither crosses over.
    assert (await get_reactions(str(CHANNEL_ID), str(MSG_ID), current_user=alice))[
        0
    ].count == 5
    assert (await get_reactions(str(CHANNEL_ID), str(MSG_ID), current_user=bob))[
        0
    ].count == 1
    assert len(fake_reactions.detail_calls) == 2


@pytest.mark.asyncio
async def test_distinct_message_ids_do_not_collide(fake_reactions):
    token = _token(42)
    await get_reactions(str(CHANNEL_ID), str(MSG_ID), current_user=token)
    await get_reactions(str(CHANNEL_ID), str(MSG_ID + 1), current_user=token)

    # The message id is part of the key; different messages are different
    # entries even for the same credential.
    assert len(fake_reactions.detail_calls) == 2


@pytest.mark.asyncio
async def test_bot_and_owner_tokens_do_not_share_cache(fake_reactions):
    owner = _token(42)
    bot = _token(42, token_type="bot", account_id=999)
    fake_reactions.pills[42] = [(EMOJI, 5, True)]

    owner_pills = await get_reactions(str(CHANNEL_ID), str(MSG_ID), current_user=owner)
    assert owner_pills[0].count == 5

    # A bot token carries its owner's user_id. Change what the core returns
    # for that user between the two calls: the bot must get its own cold read
    # (count 1), not the owner's still-warm body (count 5).
    fake_reactions.pills[42] = [(EMOJI, 1, False)]
    bot_pills = await get_reactions(str(CHANNEL_ID), str(MSG_ID), current_user=bot)

    assert bot_pills[0].count == 1
    assert len(fake_reactions.detail_calls) == 2  # distinct credential keys


@pytest.mark.asyncio
async def test_denials_are_never_cached_or_served_from_cache(fake_reactions):
    token = _token(42)

    fake_reactions.allowed = False
    with pytest.raises(HTTPException) as exc:
        await get_reactions(str(CHANNEL_ID), str(MSG_ID), current_user=token)
    assert exc.value.status_code == 404
    assert fake_reactions.detail_calls == []  # gate short-circuits the loader

    fake_reactions.allowed = True
    first = await get_reactions(str(CHANNEL_ID), str(MSG_ID), current_user=token)
    second = await get_reactions(str(CHANNEL_ID), str(MSG_ID), current_user=token)
    assert [r.model_dump() for r in first] == [r.model_dump() for r in second]
    assert len(fake_reactions.detail_calls) == 1  # the denial was not cached

    # Access is revoked while a warm body still exists: the gate wins.
    fake_reactions.allowed = False
    with pytest.raises(HTTPException) as exc:
        await get_reactions(str(CHANNEL_ID), str(MSG_ID), current_user=token)
    assert exc.value.status_code == 404
    assert len(fake_reactions.detail_calls) == 1  # warm body never consulted


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route,args",
    [
        (add_reaction, (str(CHANNEL_ID), str(MSG_ID), EMOJI)),
        (clear_reactions_for_emoji, (str(CHANNEL_ID), str(MSG_ID), EMOJI)),
        (remove_reaction, (str(CHANNEL_ID), str(MSG_ID), EMOJI)),
        (clear_reactions, (str(CHANNEL_ID), str(MSG_ID))),
    ],
)
async def test_mutations_invalidate_cached_reads(
    fake_reactions, mutation_env, route, args
):
    token = _token(42)
    before = await get_reactions(str(CHANNEL_ID), str(MSG_ID), current_user=token)
    assert before[0].count == 3
    assert len(fake_reactions.detail_calls) == 1

    # A real mutation changes the underlying data.
    fake_reactions.pills[42] = [(EMOJI, 9, True)]
    await route(*args, request=mutation_env, current_user=token)
    assert len(fake_reactions.mutations) == 1
    # Every write route runs the channel-view gate before mutating.
    assert fake_reactions.conversation_lookups == [MSG_ID]

    # The warm body is gone: the next read re-hydrates and sees the new count.
    after = await get_reactions(str(CHANNEL_ID), str(MSG_ID), current_user=token)
    assert after[0].count == 9
    assert len(fake_reactions.detail_calls) == 2
