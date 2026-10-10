"""Contract tests for pin/unpin MESSAGE_UPDATE broadcast parity.

Agent 57 F3 regression guard: every MESSAGE_UPDATE producer must build its
event through ``src.core.events.build_message_update`` so the wire payload
carries the canonical ``server_id`` scope key (plus the ``guild_id`` alias).
The pin/unpin routes previously built the event inline with a bare
``Event(...)`` and silently omitted both keys, so pinned-message updates
looked DM-scoped to clients.

Scenario: a server channel with two members; pin and unpin must each emit
exactly one MESSAGE_UPDATE whose data carries ``server_id``/``guild_id`` set
to the channel's server, fanned out to the members that can view the channel.
"""

import asyncio

import pytest

import src.api as api_module
import src.api.websocket as ws_module
from src.api.routes.messages.pins import pin_message, unpin_message
from src.core.auth.models import AccountType, TokenInfo
from src.core.events.types import EventType

pytestmark = [pytest.mark.asyncio]


async def _drain_broadcast_tasks():
    """The route broadcasts are fire-and-forget create_task()s; let them run."""
    for _ in range(5):
        await asyncio.sleep(0)
    pending = [
        t
        for t in asyncio.all_tasks()
        if t is not asyncio.current_task() and not t.done()
    ]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


class RecordingDispatcher:
    """Captures dispatched events without needing a live gateway."""

    def __init__(self):
        self.dispatched = []

    async def dispatch_event(self, event, user_ids):
        self.dispatched.append((event, [str(u) for u in user_ids]))


@pytest.fixture
def recording_ws(monkeypatch):
    """Pretend the websocket layer is set up; record every dispatch."""
    dispatcher = RecordingDispatcher()
    monkeypatch.setattr(ws_module, "is_setup", lambda: True)
    monkeypatch.setattr(ws_module, "get_dispatcher", lambda: dispatcher)
    return dispatcher


@pytest.fixture
def wired_server_channel(
    db,
    messaging_manager,
    server_manager,
    two_users,
    monkeypatch,
):
    """Real servers module wired into src.api, plus a server channel.

    The owner (user1) creates the server and a text channel; user2 joins.
    Default role permissions are backfilled so ``channels.view`` filtering
    resolves recipients for both members.
    """
    owner, member = two_users
    server = server_manager.create_server(owner.id, f"pin-scope-{id(server_manager)}")
    server_manager.add_member(server.id, member.id)
    channel = server_manager.create_channel(owner.id, server.id, "general", "text")

    try:
        from src.core.servers.manager import backfill_default_role_permissions

        backfill_default_role_permissions(db)
    except Exception:
        try:
            server_manager.backfill_default_role_permissions(db)
        except Exception:
            pass

    monkeypatch.setattr(api_module, "_messaging", messaging_manager)
    monkeypatch.setattr(api_module, "_servers", server_manager)

    return {"owner": owner, "member": member, "server": server, "channel": channel}


def _token_info(user_id, username):
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=user_id,
        user_id=user_id,
        session_id=None,
        permissions={},
        rate_limit_tier="default",
        expires_at=None,
        username=username,
        account_type=AccountType.USER,
    )


class TestPinBroadcastServerScope:
    async def test_pin_broadcast_carries_server_id_and_guild_id(
        self, wired_server_channel, recording_ws
    ):
        env = wired_server_channel
        owner, server, channel = env["owner"], env["server"], env["channel"]
        msg = env and None
        messaging = api_module.get_messaging()
        msg = messaging.send_message(owner.id, channel.conversation_id, "pin me")

        response = await pin_message(
            str(channel.id), str(msg.id), current_user=_token_info(owner.id, "owner")
        )
        assert response.success is True

        await _drain_broadcast_tasks()
        events = recording_ws.dispatched
        assert len(events) == 1, "pin emits exactly one MESSAGE_UPDATE"
        event, recipients = events[0]
        assert event.event_type == EventType.MESSAGE_UPDATE

        sid = str(server.id)
        assert event.data.get("server_id") == sid, (
            "pin MESSAGE_UPDATE must carry canonical server_id"
        )
        assert event.data.get("guild_id") == sid, (
            "pin MESSAGE_UPDATE must carry the guild_id alias"
        )
        assert event.data.get("pinned") is True
        assert event.data.get("id") == str(msg.id)
        assert event.data.get("channel_id") == str(channel.id)

        assert set(recipients) == {str(owner.id), str(env["member"].id)}

    async def test_unpin_broadcast_carries_server_id_and_guild_id(
        self, wired_server_channel, recording_ws
    ):
        env = wired_server_channel
        owner, server, channel = env["owner"], env["server"], env["channel"]
        messaging = api_module.get_messaging()
        msg = messaging.send_message(owner.id, channel.conversation_id, "unpin me")

        pinned = await pin_message(
            str(channel.id), str(msg.id), current_user=_token_info(owner.id, "owner")
        )
        assert pinned.success is True
        await _drain_broadcast_tasks()
        recording_ws.dispatched.clear()

        response = await unpin_message(
            str(channel.id), str(msg.id), current_user=_token_info(owner.id, "owner")
        )
        assert response.success is True

        await _drain_broadcast_tasks()
        events = recording_ws.dispatched
        assert len(events) == 1, "unpin emits exactly one MESSAGE_UPDATE"
        event, _recipients = events[0]
        assert event.event_type == EventType.MESSAGE_UPDATE

        sid = str(server.id)
        assert event.data.get("server_id") == sid
        assert event.data.get("guild_id") == sid
        assert event.data.get("pinned") is False
