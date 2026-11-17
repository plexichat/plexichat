"""Query-count regression test for the send response serialization.

A freshly-created message cannot have suppressed embeds (suppression is a
later moderator action), so serializing the send response should not issue a
one-off ``are_embeds_suppressed()`` lookup. The route now passes the flag
explicitly; this test fails if that precomputation is removed.
"""

from types import SimpleNamespace

import pytest

import src.api as api
from src.api.routes.messages.messages_crud.composer import MessagesCRUDRouter


class _Channel:
    server_id = None  # DM-style channel: skips automod for this focused test
    conversation_id = 777
    read_receipts_enabled = True


class _Servers:
    def get_channel(self, channel_id, user_id):
        return _Channel()


class _Message:
    id = 5001
    conversation_id = 777
    author_id = 2
    content = "hello"
    created_at = 1_700_000_000_000
    attachments = []
    embeds = []
    metadata = None


class _Messaging:
    def send_message(self, **kwargs):
        return _Message()

    def get_conversation(self, channel_id, user_id):
        return None

    def get_messages(self, user_id, conversation_id, limit=50):
        return []


class _Auth:
    def get_user(self, user_id):
        return SimpleNamespace(
            id=user_id, username="author", avatar_url="avatar", badges=["badge"]
        )


class _Token:
    user_id = 2
    username = "me"
    avatar_url = "avatar"
    badges = ["badge"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_response_does_not_query_embed_suppression(monkeypatch):
    embeds_lookups = []

    def _get_embeds_spy():
        embeds_lookups.append(1)
        return None

    monkeypatch.setattr(api, "get_servers", lambda: _Servers())
    monkeypatch.setattr(api, "get_messaging", lambda: _Messaging())
    monkeypatch.setattr(api, "get_auth", lambda: _Auth())
    monkeypatch.setattr(api, "get_media", lambda: None)
    monkeypatch.setattr(api, "get_embeds", _get_embeds_spy)

    async def _no_broadcast(*args, **kwargs):
        return None

    monkeypatch.setattr(MessagesCRUDRouter, "_broadcast_message_create", _no_broadcast)

    body = SimpleNamespace(
        content="hello",
        attachments=None,
        embeds=None,
        poll=None,
        reply_to_id=None,
    )
    router = MessagesCRUDRouter()

    response = await router.send_channel_message("10", body, _Token())  # pyright: ignore[reportArgumentType]

    assert int(response.id) == 5001
    assert embeds_lookups == [], "send response must not query embed suppression"
