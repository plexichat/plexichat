"""SECURITY: forwarding must enforce live channels.view on source and target.

``ForwardingService.forward_message`` previously guarded only conversation
*participation* (``is_participant``). For server channels that grants every
member of the server participation regardless of the per-channel
``channels.view`` permission. Forwarding reads and decrypts the source
message content and re-sends it to the target conversation, so a member denied
view on a private channel could exfiltrate its messages by forwarding them
into a channel/DM they control, and plant content in a channel they cannot
view.

The message get/pin routes already enforce the live ``get_channel`` view gate;
the forward route did not. These tests pin that gate for both sides of a
forward and verify the route wires the live servers module into the service.

Run with: pytest src/tests/messaging/test_forward_channel_view_gate.py -q -n 0
"""

import pytest

import src.api as api
import src.api.routes.features.forwarding as route
from src.core.messaging.services.forwarding import ForwardingService

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.messaging]

SOURCE_CONV = 2
TARGET_CONV = 99
MESSAGE_ID = 20
VIEWER = 5
AUTHOR = 7
SOURCE_CHANNEL = 555
TARGET_CHANNEL = 777

# ``SELECT id, conversation_id, author_id, content, content_encrypted,
# created_at`` row shape used by ``forward_message``.
_MESSAGE_ROW = (MESSAGE_ID, SOURCE_CONV, AUTHOR, "secret-private-content", None, 100)


class _DB:
    def __init__(self):
        self.executed = []

    def fetch_one(self, query, params=()):
        if "FROM msg_messages" in query:
            return _MESSAGE_ROW
        if "COUNT(*)" in query:
            return (0,)
        return None

    def fetch_all(self, query, params=()):
        return []

    def execute(self, query, params=(), auto_commit=True):
        self.executed.append((query, params))

        class _Cursor:
            rowcount = 1

            def close(self):
                pass

        return _Cursor()


class _Participants:
    """Server member: participant in both conversations, regardless of view."""

    def is_participant(self, conversation_id, user_id):
        return True


class _Servers:
    def __init__(self, channels, viewable):
        self.channels = (
            channels  # conversation_id -> {"id": ..., "server_id": ...} or None
        )
        self.viewable = viewable  # channel ids this user may view
        self.channel_gets = []

    def get_channel_by_conversation(self, conversation_id):
        return self.channels.get(conversation_id)

    def channel_exists(self, channel_id):
        # Membership-agnostic probe used to tell a dangling conversation link
        # (channel id stored as the conversation id) from a genuine DM.
        return any(
            isinstance(info, dict) and info.get("id") == channel_id
            for info in self.channels.values()
        )

    def get_channel(self, channel_id, user_id):
        self.channel_gets.append((channel_id, user_id))
        return object() if channel_id in self.viewable else None


class TestForwardChannelViewGate:
    def test_source_channel_view_denied_blocks_forward(self):
        """The core leak: participant in private source channel, cannot view it."""
        db = _DB()
        servers = _Servers(
            {SOURCE_CONV: {"id": SOURCE_CHANNEL, "server_id": 1}, TARGET_CONV: None},
            viewable=set(),
        )
        svc = ForwardingService(db, None, _Participants(), servers)
        svc._safe_send_via_messaging = lambda *a, **k: pytest.fail(
            "content must not be sent when the source channel is not viewable"
        )

        with pytest.raises(PermissionError):
            svc.forward_message(VIEWER, MESSAGE_ID, TARGET_CONV)

        assert db.executed == []  # no forward record written
        assert (SOURCE_CHANNEL, VIEWER) in servers.channel_gets  # live gate ran

    def test_target_channel_view_denied_blocks_forward(self):
        db = _DB()
        servers = _Servers(
            {SOURCE_CONV: None, TARGET_CONV: {"id": TARGET_CHANNEL, "server_id": 1}},
            viewable=set(),
        )
        svc = ForwardingService(db, None, _Participants(), servers)
        svc._safe_send_via_messaging = lambda *a, **k: pytest.fail(
            "content must not be planted in a channel that cannot be viewed"
        )

        with pytest.raises(PermissionError):
            svc.forward_message(VIEWER, MESSAGE_ID, TARGET_CONV)

        assert db.executed == []
        assert (TARGET_CHANNEL, VIEWER) in servers.channel_gets

    def test_view_granted_allows_forward(self):
        """Control: with channels.view on both channels the forward completes."""
        db = _DB()
        servers = _Servers(
            {
                SOURCE_CONV: {"id": SOURCE_CHANNEL, "server_id": 1},
                TARGET_CONV: {"id": TARGET_CHANNEL, "server_id": 1},
            },
            viewable={SOURCE_CHANNEL, TARGET_CHANNEL},
        )
        svc = ForwardingService(db, None, _Participants(), servers)
        svc._safe_send_via_messaging = lambda *a, **k: {"id": 123}

        result = svc.forward_message(VIEWER, MESSAGE_ID, TARGET_CONV)

        assert result["new_message_id"] == 123
        assert len(db.executed) == 1  # forward record written
        assert (SOURCE_CHANNEL, VIEWER) in servers.channel_gets
        assert (TARGET_CHANNEL, VIEWER) in servers.channel_gets

    def test_missing_reverse_lookup_fails_closed(self):
        """A servers module without the reverse lookup cannot prove DM vs channel."""

        class _NoLookup:
            pass

        db = _DB()
        svc = ForwardingService(db, None, _Participants(), _NoLookup())
        svc._safe_send_via_messaging = lambda *a, **k: pytest.fail("must not send")

        with pytest.raises(PermissionError):
            svc.forward_message(VIEWER, MESSAGE_ID, TARGET_CONV)

        assert db.executed == []

    @pytest.mark.asyncio
    async def test_route_wires_servers_module_into_service(self, monkeypatch):
        """The production route passes api.get_servers() to the gated service."""

        class _ServersStub:
            def get_channel_by_conversation(self, conversation_id):
                return None  # DM target: no per-channel send gate applies

        sentinel = _ServersStub()
        captured = {}

        class _FakeService:
            def __init__(self, db, messaging, participant_svc, servers_mod=None):
                captured["servers_mod"] = servers_mod

            def forward_message(self, *args, **kwargs):
                return {}

        class _User:
            token_type = "user"
            user_id = VIEWER

        monkeypatch.setattr(route, "ForwardingService", _FakeService)
        monkeypatch.setattr(route, "resolve_participant_service", lambda m: object())
        monkeypatch.setattr(api, "get_db", lambda: None)
        monkeypatch.setattr(api, "get_messaging", lambda: object())
        monkeypatch.setattr(api, "get_servers", lambda: sentinel)

        await route.forward_message(
            route.ForwardMessageRequest(
                message_id=str(MESSAGE_ID),
                target_conversation_id=str(TARGET_CONV),
            ),
            current_user=_User(),
        )

        assert captured["servers_mod"] is sentinel
