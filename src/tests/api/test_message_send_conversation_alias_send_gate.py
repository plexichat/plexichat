"""Regression: the REST send route must apply the live channel view/send gate
when a server channel's linked ``conversation_id`` is used as the URL alias.

``POST /channels/{id}/messages`` accepts a server channel's linked
``conversation_id`` in the ``channel_id`` slot (the ``messaging.get_conversation``
fallback). When that alias is supplied, ``get_channel(alias, user)`` returns
None because the alias is not a ``srv_channels`` row id, so the normal
per-channel ``messages.send`` / timeout checks are skipped and the send falls
through to the messaging layer. There, conversation participation is the only
boundary -- and participation is seeded for every server member on join, not
granted by ``messages.send``. A member denied view or ``messages.send`` on a
private channel could otherwise post into it by putting its conversation id in
the URL.

The route must reverse-resolve the alias membership-agnostically and require a
live view grant and ``messages.send`` on the real backing channel, failing
closed on any unresolvable permission data.

Run with: pytest src/tests/api/test_message_send_conversation_alias_send_gate.py -q -n 0
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
        owner_id=owner.id, name="sendalias-" + uuid.uuid4().hex[:6]
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


pytestmark = pytest.mark.messaging


class TestMessageSendConversationAliasGate:
    def test_bypass_preconditions_hold(self, readonly_channel):
        """Without the gate these facts make the alias send bypass possible."""
        servers, messaging, channel, conv_id, owner, member, server = readonly_channel
        # The alias resolves to the backing channel membership-agnostically.
        backing = servers.get_channel_by_conversation(conv_id)
        assert backing is not None
        assert int(backing["id"]) == int(channel.id)
        # get_channel cannot resolve the alias as a channel id (that is why the
        # normal per-channel messages.send gate is skipped).
        assert servers.get_channel(conv_id, member.id) is None
        # ...but participation is seeded for the member, and send is denied.
        assert messaging.is_participant(conv_id, member.id) is True
        assert (
            servers.has_permission(member.id, server.id, "messages.send", channel.id)
            is False
        )

    @pytest.mark.asyncio
    async def test_alias_send_denied_without_messages_send(self, readonly_channel):
        servers, messaging, channel, conv_id, owner, member, server = readonly_channel
        router = MessagesCRUDRouter()

        # No server channel resolves from the alias, so server_id is None and
        # the ordinary channel send gate never runs.
        ctx = {}
        assert router._resolve_server_id(conv_id, member.id, servers, ctx) is None

        with pytest.raises(HTTPException) as ei:
            await router._send_message_with_fallback(
                conv_id,
                member.id,
                "alias bypass attempt",
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
    async def test_alias_send_allowed_with_messages_send(self, readonly_channel):
        """A member who may post can still send through the alias."""
        servers, messaging, channel, conv_id, owner, member, server = readonly_channel
        servers.set_channel_override(
            owner.id,
            channel.id,
            "member",
            member.id,
            allow={"messages.send": True},
            deny={},
        )
        router = MessagesCRUDRouter()
        ctx = {}

        msg = await router._send_message_with_fallback(
            conv_id,
            member.id,
            "legitimate alias send",
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
    async def test_alias_send_fails_closed_when_reverse_lookup_raises(
        self, readonly_channel, monkeypatch
    ):
        servers, messaging, channel, conv_id, owner, member, server = readonly_channel
        router = MessagesCRUDRouter()

        def _boom(_cid):
            raise RuntimeError("reverse lookup unavailable")

        monkeypatch.setattr(servers, "get_channel_by_conversation", _boom)
        ctx = {}
        with pytest.raises(HTTPException) as ei:
            await router._send_message_with_fallback(
                conv_id,
                member.id,
                "alias bypass attempt",
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
    async def test_dm_send_without_backing_channel_is_not_gated(
        self, modules, user_pool
    ):
        """A genuine DM conversation (no backing channel) still sends."""
        servers = modules.servers
        messaging = modules.messaging
        u1 = user_pool.get_user()
        u2 = user_pool.get_user()
        dm = messaging.create_dm(u1.id, u2.id)
        dm_conv_id = int(dm.id)
        assert servers.get_channel_by_conversation(dm_conv_id) is None

        router = MessagesCRUDRouter()
        ctx = {}
        msg = await router._send_message_with_fallback(
            dm_conv_id, u1.id, "dm send", None, None, None, servers, messaging, ctx
        )
        assert msg is not None
        assert messaging.get_messages(u1.id, dm_conv_id, limit=10) != []
