"""REGRESSION: unwired servers module must fail closed in the REST send
channel-alias gate.

``MessagesCRUDBase._require_alias_send_access`` guards the inline
``messaging.send_message`` fallback the send route uses when the URL id is a
server channel's linked ``conversation_id`` (an alias that ``get_channel``
cannot resolve as a channel id). It reverse-resolves the alias to the backing
channel and requires a live ``get_channel`` (view) + ``messages.send`` grant.
When the servers module is unwired (``api.get_servers()`` returns ``None``)
that reverse lookup cannot be evaluated. The DB tables (``srv_channels`` and
its ``conversation_id`` column) exist independently of the Python module, so a
private-channel alias is still present in the database in that state. The gate
used to ``return`` (skip the denial) on ``None``, letting any participant post
into a private channel whose ``messages.send`` grant was never checked.

Invariant (same as ``test_unwired_dependency_fails_closed``):

    A check that cannot be evaluated because its dependency is unwired must
    DENY, not skip.

The wired controls keep the invariant non-vacuous: fail-closed must not become
a blanket denial of DMs (a wired but uninitialised store has no channel table,
so the conversation can only be a DM/group).

Run with::

    pytest -n 0 -q src/tests/api/test_unwired_servers_alias_send_gate_fail_closed.py
"""

import uuid

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.messages.messages_crud.composer import MessagesCRUDRouter

pytestmark = [pytest.mark.messaging, pytest.mark.security]

ALIAS = 987654321
CHANNEL = 6000
SERVER = 7000
USER = 42


class _UninitializedStore:
    """Wired module object whose server-channel store was never set up."""

    _setup_complete = False

    def get_channel_by_conversation(self, conversation_id):
        raise AssertionError("an uninitialised store has no channel table to query")


class _WiredAliasStore:
    """Wired, initialised store whose alias maps to a private channel."""

    _setup_complete = True

    def get_channel_by_conversation(self, conversation_id):
        return {"id": CHANNEL, "server_id": SERVER}


@pytest.fixture
def readonly_channel(modules, user_pool):
    """A real private channel whose alias is gated by ``messages.send``.

    The member keeps ``channels.view`` but loses ``messages.send`` via an
    explicit override, so the only thing that may block an alias send is the
    gate under test. This is the "protected data still in the DB" setup.
    """
    owner = user_pool.get_user()
    member = user_pool.get_user()
    servers = modules.servers
    messaging = modules.messaging

    server = servers.create_server(
        owner_id=owner.id, name="sendalias-unwired-" + uuid.uuid4().hex[:6]
    )
    servers.add_member(server.id, member.id)
    channel = servers.create_channel(
        user_id=owner.id,
        server_id=server.id,
        name="readonly-" + uuid.uuid4().hex[:6],
    )
    conv_id = int(channel.conversation_id)
    servers.set_channel_override(
        owner.id, channel.id, "member", member.id, deny={"messages.send": True}
    )
    return servers, messaging, channel, conv_id, owner, member, server


class TestUnwiredServersAliasSendGate:
    def test_unwired_module_denies_alias_send_gate(self):
        """The unwired dependency must deny, not skip, the alias gate."""
        router = MessagesCRUDRouter()
        with pytest.raises(HTTPException) as ei:
            router._require_alias_send_access(ALIAS, USER, None)
        assert ei.value.status_code == 403

    def test_unwired_denies_with_protected_private_channel_in_db(
        self, readonly_channel, monkeypatch
    ):
        """The alias maps to a real private channel in the DB while unwired.

        Pre-fix the gate returned without denying; fail-closed must raise even
        though the protected row is still present in the database.
        """
        servers, messaging, channel, conv_id, owner, member, server = readonly_channel
        # Prove the protected private channel and its alias really exist in the
        # DB, independent of whether the servers module is wired into the API.
        backing = servers.get_channel_by_conversation(conv_id)
        assert backing is not None
        assert int(backing["id"]) == int(channel.id)
        assert messaging.is_participant(conv_id, member.id) is True
        assert (
            servers.has_permission(member.id, server.id, "messages.send", channel.id)
            is False
        )

        monkeypatch.setattr(api, "get_servers", lambda: None)
        assert api.get_servers() is None

        router = MessagesCRUDRouter()
        with pytest.raises(HTTPException) as ei:
            router._require_alias_send_access(conv_id, member.id, api.get_servers())
        assert ei.value.status_code == 403

    @pytest.mark.asyncio
    async def test_unwired_alias_send_does_not_post_into_private_channel(
        self, readonly_channel, monkeypatch
    ):
        """End-to-end: unwired servers must not post through the alias.

        The pre-fix gate skipped itself on ``None`` and the fallback
        ``messaging.send_message`` delivered the message into the private
        channel; the message must now be absent.
        """
        servers, messaging, channel, conv_id, owner, member, server = readonly_channel
        assert servers.get_channel_by_conversation(conv_id) is not None
        monkeypatch.setattr(api, "get_servers", lambda: None)

        router = MessagesCRUDRouter()
        with pytest.raises(HTTPException) as ei:
            await router._send_message_with_fallback(
                conv_id,
                member.id,
                "unwired alias bypass attempt",
                None,
                None,
                None,
                None,  # servers_mod is unwired
                messaging,
                {},
            )
        assert ei.value.status_code == 403
        assert messaging.get_messages(member.id, conv_id, limit=10) == []

    def test_wired_uninitialized_store_still_allows_genuine_dm(self):
        """Control: a wired but uninitialised store has no channel alias."""
        router = MessagesCRUDRouter()
        # No exception => the DM/group conversation is still allowed.
        router._require_alias_send_access(ALIAS, USER, _UninitializedStore())

    def test_wired_alias_still_denies_when_grant_unresolvable(self):
        """Control: a wired alias still fails closed without a permission source."""
        router = MessagesCRUDRouter()
        with pytest.raises(HTTPException) as ei:
            router._require_alias_send_access(ALIAS, USER, _WiredAliasStore())
        assert ei.value.status_code == 403
