"""Regression: channel slowmode must also gate sends addressed by a server
channel's linked ``conversation_id`` alias.

``POST /channels/{id}/messages`` accepts the linked conversation id in the
``channel_id`` slot. On that path ``_resolve_server_id`` returns None (the alias
is not a ``srv_channels`` row id), so the REST route's ``if server_id:`` block
-- the only place it calls ``_enforce_channel_slowmode`` -- is skipped. The
alias fallback in ``_send_message_with_fallback`` does check a live view grant
and ``messages.send`` via ``_require_alias_send_access``, but never applied the
slowmode gate. A member in a slow channel could therefore post as fast as they
liked simply by swapping the channel id for its conversation id.

The fix must apply the same slowmode gate to the real backing channel id, and
fail closed when that gate cannot be evaluated on this path (the normal
per-channel id path is never reached, so there is nothing else to fall back
to).

Run with: pytest src/tests/api/test_slowmode_conversation_alias_bypass.py -q -n 0
"""

import uuid

import pytest
from fastapi import HTTPException

from src.api.routes.messages.messages_crud.composer import MessagesCRUDRouter

pytestmark = pytest.mark.messaging


@pytest.fixture
def slowmode_alias(modules, user_pool):
    owner = user_pool.get_user()
    member = user_pool.get_user()
    servers = modules.servers
    messaging = modules.messaging

    server = servers.create_server(
        owner_id=owner.id, name="slow-alias-" + uuid.uuid4().hex[:6]
    )
    servers.add_member(server.id, member.id)
    channel = servers.create_channel(
        user_id=owner.id,
        server_id=server.id,
        name="slow-alias-" + uuid.uuid4().hex[:6],
        slowmode_seconds=30,
    )
    conv_id = int(channel.conversation_id)
    # Remove any ambiguity about the send grant: the member may post, so the
    # only thing that may stop the second send is slowmode.
    servers.set_channel_override(
        owner.id, channel.id, "member", member.id, allow={"messages.send": True}, deny={}
    )
    return servers, messaging, channel, conv_id, owner, member, server


class TestSlowmodeConversationAliasBypass:
    def test_alias_is_not_a_channel_and_send_is_granted(self, slowmode_alias):
        """Pin the preconditions that make the bypass possible."""
        servers, _messaging, channel, conv_id, _owner, member, server = slowmode_alias
        assert servers.get_channel(conv_id, member.id) is None
        assert int(servers.get_channel_by_conversation(conv_id)["id"]) == int(channel.id)
        assert servers.has_permission(member.id, server.id, "messages.send", channel.id)

    @pytest.mark.asyncio
    async def test_second_send_via_alias_is_slowmoded(self, slowmode_alias):
        """Two rapid sends through the alias must be throttled like channel id sends."""
        servers, messaging, channel, conv_id, _owner, member, _server = slowmode_alias
        router = MessagesCRUDRouter()

        first = await router._send_message_with_fallback(
            conv_id, member.id, "first alias send", None, None, None, servers, messaging, {}
        )
        assert first is not None

        # The first send must have armed the real channel's slowmode window.
        # (check_channel_slowmode shares the slowmode:{channel}:{user} key.)
        retry_after = servers.check_channel_slowmode(member.id, channel.id)
        assert retry_after is not None and 0 < retry_after <= 30

        with pytest.raises(HTTPException) as exc:
            await router._send_message_with_fallback(
                conv_id,
                member.id,
                "second alias send",
                None,
                None,
                None,
                servers,
                messaging,
                {},
            )
        assert exc.value.status_code == 429, (
            "slowmode must be enforced through the conversation-id alias"
        )
        contents = [
            m.content
            for m in (messaging.get_messages(member.id, conv_id, limit=10) or [])
        ]
        assert "second alias send" not in contents

    @pytest.mark.asyncio
    async def test_alias_send_fails_closed_when_slowmode_gate_unavailable(
        self, slowmode_alias, monkeypatch
    ):
        """If the slowmode gate cannot run, the alias send must be denied."""
        servers, messaging, _channel, conv_id, _owner, member, _server = slowmode_alias
        router = MessagesCRUDRouter()

        def _boom(*args, **kwargs):
            raise RuntimeError("slowmode cache unavailable")

        monkeypatch.setattr(servers, "check_channel_slowmode", _boom)
        with pytest.raises(HTTPException) as exc:
            await router._send_message_with_fallback(
                conv_id, member.id, "bypass attempt", None, None, None, servers, messaging, {}
            )
        assert exc.value.status_code == 403
        contents = [
            m.content
            for m in (messaging.get_messages(member.id, conv_id, limit=10) or [])
        ]
        assert "bypass attempt" not in contents
