"""SECURITY: message delete must not accept a channel's conversation-id alias.

The delete routes gate the URL id with ``_require_channel_view``, which probes
``get_channel``/``channel_exists``. Message routes deliberately also accept a
server channel's linked ``conversation_id`` as a URL alias
(``_resolve_channel_scope``), but a conversation id is NOT a channel id: both
probes miss it, so the gate treats the alias as a DM and waves it through. A
member denied ``channels.view`` on a private channel could then delete messages
in it by putting the conversation id in the URL.

The gate must reverse-resolve the alias membership-agnostically and require a
live view grant on the real backing channel before any mutation.

Run with: pytest src/tests/api/test_message_delete_conversation_id_alias_gate.py -q -n 0
"""

import pytest
from fastapi import HTTPException

import src.api as api
import src.api.routes.messages.messages_crud.broadcast as broadcast
import src.api.routes.messages.messages_crud.delete as route
import src.api.routes.messages.helpers as helpers
from src.core.auth.models import AccountType, TokenInfo

pytestmark = [pytest.mark.unit, pytest.mark.security]


def _token(uid, username="user"):
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=uid,
        user_id=uid,
        session_id=None,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username=username,
        account_type=AccountType.USER,
    )


class _Message:
    def __init__(self, conversation_id, message_id, author_id):
        self.id = message_id
        self.conversation_id = conversation_id
        self.author_id = author_id
        self.content = "private-channel-content"
        self.created_at = 1
        self.attachments = []
        self.metadata = None


class _Messaging:
    """Participant gate satisfied; only the live channel-view gate can deny."""

    def __init__(self, conversation_id, message_id, author_id):
        self._msg = _Message(conversation_id, message_id, author_id)
        self.delete_calls = []
        self.bulk_delete_calls = []

    def get_message(self, user_id, message_id):
        return self._msg

    def delete_message(self, user_id, message_id, hard_delete=False):
        self.delete_calls.append((user_id, message_id))
        return True

    def delete_messages_bulk(self, user_id, channel_id, message_ids):
        self.bulk_delete_calls.append((user_id, channel_id, tuple(message_ids)))
        return list(message_ids)


class _Body:
    def __init__(self, ids):
        self.message_ids = ids


@pytest.fixture
def alias_env(db, server_manager, two_users, monkeypatch):
    owner, member = two_users
    server = server_manager.create_server(owner.id, "delete-alias-gate")
    server_manager.add_member(server.id, member.id)
    channel = server_manager.create_channel(owner.id, server.id, "secret", "text")
    server_manager.set_channel_override(
        user_id=owner.id,
        channel_id=channel.id,
        target_type="member",
        target_id=member.id,
        deny={"channels.view": True},
    )

    # Non-vacuous sanity checks: the member really is denied view on the real
    # channel, and the conversation id really is backed by that channel (so the
    # test cannot pass merely because the fixture lost its conversation link).
    assert server_manager.get_channel(channel.id, member.id) is None
    backing = server_manager.get_channel_by_conversation(channel.conversation_id)
    assert backing is not None and int(backing["id"]) == channel.id

    message_id = channel.conversation_id + 1_000_000
    messaging = _Messaging(channel.conversation_id, message_id, owner.id)
    monkeypatch.setattr(api, "get_messaging", lambda: messaging)
    monkeypatch.setattr(api, "get_servers", lambda: server_manager)
    monkeypatch.setattr(api, "get_auth", lambda: None)
    monkeypatch.setattr(api, "get_media", lambda: None)
    monkeypatch.setattr(api, "get_db", lambda: None)
    monkeypatch.setattr(api, "get_polls", lambda: None)
    monkeypatch.setattr(api, "get_reactions", lambda: None)
    monkeypatch.setattr(helpers, "_message_to_response", lambda *a, **k: object())

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(
        broadcast.BroadcastMixin, "_broadcast_message_update", _noop, raising=False
    )
    monkeypatch.setattr(
        broadcast.BroadcastMixin, "_broadcast_message_delete", _noop, raising=False
    )
    return {
        "owner": owner,
        "member": member,
        "channel": channel,
        "messaging": messaging,
        "message_id": message_id,
    }


@pytest.mark.asyncio
async def test_single_delete_via_conversation_alias_denied_without_view(alias_env):
    env = alias_env
    with pytest.raises(HTTPException) as exc:
        await route.DeleteMixin().delete_message(
            str(env["channel"].conversation_id),
            str(env["message_id"]),
            current_user=_token(env["member"].id),
        )
    assert exc.value.status_code in (403, 404)
    # The live view gate must run before any mutation.
    assert env["messaging"].delete_calls == []


@pytest.mark.asyncio
async def test_bulk_delete_via_conversation_alias_denied_without_view(alias_env):
    env = alias_env
    mid = env["message_id"]
    with pytest.raises(HTTPException) as exc:
        await route.DeleteMixin().bulk_delete_messages(
            str(env["channel"].conversation_id),
            body=_Body([mid, mid + 1]),
            current_user=_token(env["member"].id),
        )
    assert exc.value.status_code in (403, 404)
    assert env["messaging"].bulk_delete_calls == []


@pytest.mark.asyncio
async def test_single_delete_via_conversation_alias_allowed_with_view(alias_env):
    """Control: a real channel viewer may still delete through the alias."""
    env = alias_env
    result = await route.DeleteMixin().delete_message(
        str(env["channel"].conversation_id),
        str(env["message_id"]),
        current_user=_token(env["owner"].id),
    )
    assert result.success is True
    assert env["messaging"].delete_calls == [(env["owner"].id, env["message_id"])]


@pytest.mark.asyncio
async def test_bulk_delete_via_conversation_alias_allowed_with_view(alias_env):
    """Control: the alias remains a valid address for a real channel viewer."""
    env = alias_env
    mid = env["message_id"]
    result = await route.DeleteMixin().bulk_delete_messages(
        str(env["channel"].conversation_id),
        body=_Body([mid, mid + 1]),
        current_user=_token(env["owner"].id),
    )
    assert result.success is True
    assert env["messaging"].bulk_delete_calls == [
        (env["owner"].id, env["channel"].conversation_id, (mid, mid + 1))
    ]
