"""Regression: the REST send route must enforce ``messages.send`` on server
channels.

``POST /channels/{id}/messages`` publishes straight through the messaging layer
instead of the servers manager's ``send_channel_message`` gate, where
``messages.send`` is normally checked. Conversation participation is seeded for
every server member on join, so it is not an authorization boundary for a
server channel. A member who can view a read-only channel (view granted,
send denied by an override) could still post to it through this route.

The gate must also fail closed: a servers module with no permission API, or a
permission lookup that errors, must deny rather than silently grant the send.

Run with: pytest src/tests/api/test_rest_send_messages_send_gate.py -q -n 0
"""

import uuid

import pytest
from fastapi import HTTPException

from src.api.routes.messages.messages_crud.composer import MessagesCRUDRouter


@pytest.fixture
def readonly_channel(modules, user_pool):
    owner = user_pool.get_user()
    member = user_pool.get_user()
    servers = modules.servers
    messaging = modules.messaging

    server = servers.create_server(
        owner_id=owner.id, name="sendgate-" + uuid.uuid4().hex[:6]
    )
    servers.add_member(server.id, member.id)
    channel = servers.create_channel(
        user_id=owner.id,
        server_id=server.id,
        name="readonly-" + uuid.uuid4().hex[:6],
    )
    conv_id = int(channel.conversation_id)

    # The member keeps channels.view (default role) but loses messages.send on
    # this channel via an explicit per-member override.
    servers.set_channel_override(
        owner.id, channel.id, "member", member.id, deny={"messages.send": True}
    )
    return servers, messaging, channel, conv_id, owner, member, server


def _resolved_ctx(servers, channel, member):
    router = MessagesCRUDRouter()
    ctx = {}
    server_id = router._resolve_server_id(channel.id, member.id, servers, ctx)
    return router, ctx, server_id


@pytest.mark.messaging
class TestRestSendMessagesSendGate:
    def test_bypass_preconditions_hold(self, readonly_channel):
        """Without the gate these three facts make the bypass possible.

        The member can view the channel, is a conversation participant, but
        the authoritative channel permission says send is denied.
        """
        servers, messaging, channel, conv_id, owner, member, server = readonly_channel
        assert servers.get_channel(channel.id, member.id) is not None
        assert messaging.is_participant(conv_id, member.id) is True
        assert (
            servers.has_permission(member.id, server.id, "messages.send", channel.id)
            is False
        )

    @pytest.mark.asyncio
    async def test_send_denied_without_messages_send(self, readonly_channel):
        servers, messaging, channel, conv_id, owner, member, server = readonly_channel
        router, ctx, server_id = _resolved_ctx(servers, channel, member)
        assert server_id == int(server.id)

        with pytest.raises(HTTPException) as ei:
            await router._send_message_with_fallback(
                channel.id,
                member.id,
                "bypass attempt",
                None,
                None,
                None,
                servers,
                messaging,
                ctx,
            )
        assert ei.value.status_code == 403
        assert messaging.get_messages(member.id, conv_id, limit=10) == []

    @pytest.mark.asyncio
    async def test_send_allowed_with_messages_send(self, readonly_channel):
        """The gate must not break a member who is allowed to post."""
        servers, messaging, channel, conv_id, owner, member, server = readonly_channel
        servers.set_channel_override(
            owner.id,
            channel.id,
            "member",
            member.id,
            allow={"messages.send": True},
            deny={},
        )
        router, ctx, _server_id = _resolved_ctx(servers, channel, member)

        msg = await router._send_message_with_fallback(
            channel.id,
            member.id,
            "legitimate send",
            None,
            None,
            None,
            servers,
            messaging,
            ctx,
        )
        assert msg is not None
        assert messaging.get_messages(member.id, conv_id, limit=10) != []

    @pytest.mark.asyncio
    async def test_fails_closed_when_permission_api_is_missing(self, readonly_channel):
        servers, messaging, channel, conv_id, owner, member, server = readonly_channel

        class _NoPermissionAPI:
            """Servers proxy that cannot answer the permission question."""

            def get_channel(self, channel_id, user_id):
                return servers.get_channel(channel_id, user_id)

        broken = _NoPermissionAPI()
        router, ctx, _server_id = _resolved_ctx(broken, channel, member)
        with pytest.raises(HTTPException) as ei:
            await router._send_message_with_fallback(
                channel.id,
                member.id,
                "bypass attempt",
                None,
                None,
                None,
                broken,
                messaging,
                ctx,
            )
        assert ei.value.status_code == 403
        assert messaging.get_messages(member.id, conv_id, limit=10) == []

    @pytest.mark.asyncio
    async def test_fails_closed_when_permission_lookup_raises(self, readonly_channel):
        servers, messaging, channel, conv_id, owner, member, server = readonly_channel

        class _BrokenPermissionAPI:
            def get_channel(self, channel_id, user_id):
                return servers.get_channel(channel_id, user_id)

            def has_permission(self, *args, **kwargs):
                raise RuntimeError("permission service unavailable")

        broken = _BrokenPermissionAPI()
        router, ctx, _server_id = _resolved_ctx(broken, channel, member)
        with pytest.raises(HTTPException) as ei:
            await router._send_message_with_fallback(
                channel.id,
                member.id,
                "bypass attempt",
                None,
                None,
                None,
                broken,
                messaging,
                ctx,
            )
        assert ei.value.status_code == 403
        assert messaging.get_messages(member.id, conv_id, limit=10) == []
