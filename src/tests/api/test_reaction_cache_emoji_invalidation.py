"""Regression: custom-emoji mutations must invalidate the reaction-list cache.

``DELETE /servers/{id}/emojis/{id}`` also deletes every reaction that used the
emoji (``ReactionManager.delete_custom_emoji``), but the HTTP layer memoizes
``GET .../messages/{id}/reactions`` in a separate ``reaction_read_api`` tier.
The reaction routes invalidate that tier on their own writes; the emoji routes
only invalidated the emoji list/detail caches, so a cached pill list survived
the emoji deletion and kept serving a reaction that no longer existed.

The test below warms the real route-level reaction cache, deletes a custom
emoji through the real route body, and asserts the next read re-hydrates from
the core instead of replaying the stale body.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import src.api as api
from src.api.routes import emojis as emojis_routes
from src.api.routes.emojis import delete_emoji
from src.api.routes.reactions import _REACTION_READ_PREFIX, get_reactions
from src.core.auth.models import AccountType, TokenInfo
from src.core.database.cache.operations import invalidate_pattern
from src.core.reactions.models import MessageReactions, ReactionCount

pytestmark = [pytest.mark.unit]

CHANNEL_ID = 42
MSG_ID = 9003
SERVER_ID = 77
EMOJI_ID = 9004
CUSTOM = f"<:wave:{EMOJI_ID}>"


def _token(uid: int = 42) -> TokenInfo:
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=uid,
        user_id=uid,
        session_id=None,
        permissions={},
        rate_limit_tier="default",
        expires_at=None,
        username=f"user-{uid}",
        account_type=AccountType.USER,
    )


class _FakeReactions:
    """Stand-in for the core module; pills mutate when the emoji is deleted."""

    def __init__(self):
        self.detail_calls: list[tuple[int, int]] = []
        self.access_calls: list[tuple[int, int]] = []
        self.deleted: list[tuple[int, int]] = []
        self.bound_calls: list[int] = []
        self.pills = [(CUSTOM, 2, False)]

    def can_access_message(self, user_id: int, message_id: int) -> bool:
        self.access_calls.append((user_id, message_id))
        return True

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
                    is_custom=True,
                    custom_emoji_id=EMOJI_ID,
                    url="/emoji/wave.png",
                )
                for emoji, count, me in self.pills
            ],
        )

    def get_custom_emoji(self, emoji_id: int):
        # delete_emoji checks URL-server binding before mutating: the fake
        # must expose the same lookup as the real manager.
        self.bound_calls.append(emoji_id)
        return SimpleNamespace(server_id=SERVER_ID)

    def delete_custom_emoji(self, user_id: int, emoji_id: int) -> bool:
        self.deleted.append((user_id, emoji_id))
        # Mirror the core mutation: every reaction using this emoji is gone.
        self.pills = []
        return True


@pytest.fixture(autouse=True)
def _clean_reaction_read_cache():
    invalidate_pattern(f"{_REACTION_READ_PREFIX}:*")
    yield
    invalidate_pattern(f"{_REACTION_READ_PREFIX}:*")


@pytest.mark.asyncio
async def test_deleting_custom_emoji_drops_cached_reaction_body():
    fake = _FakeReactions()
    servers = SimpleNamespace(get_permissions=lambda uid, sid: {"server.manage": True})

    with (
        patch.object(api, "get_reactions", return_value=fake),
        patch.object(api, "get_servers", return_value=servers),
        patch.object(
            emojis_routes.asyncio, "create_task", side_effect=lambda coro: coro.close()
        ),
    ):
        token = _token()
        before = await get_reactions(str(CHANNEL_ID), str(MSG_ID), current_user=token)
        assert [r.emoji for r in before] == [CUSTOM]
        assert len(fake.detail_calls) == 1  # warm body cached

        resp = await delete_emoji(str(SERVER_ID), str(EMOJI_ID), current_user=token)
        assert resp.success is True
        # The URL-server binding check must run before the deletion.
        assert fake.bound_calls == [EMOJI_ID]
        assert fake.deleted == [(42, EMOJI_ID)]

        # The deleted emoji's reactions must not be served from the warm body.
        after = await get_reactions(str(CHANNEL_ID), str(MSG_ID), current_user=token)
        assert after == []
        assert len(fake.detail_calls) == 2  # re-hydrated, not served from cache
