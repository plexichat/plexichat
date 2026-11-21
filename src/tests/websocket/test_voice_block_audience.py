"""Voice state / speaking frames must not cross a two-way block.

``VoiceHandler._get_typing_recipient_ids`` resolves the channel audience from
``channels.view`` alone and hands an explicit ``user_ids`` list to the gateway
dispatcher (``dispatch_event`` for VOICE_STATE_UPDATE, ``dispatch_raw_to_users``
for VOICE_SPEAKING). The event router's DM block gate never sees an explicit
audience, so without a gate here a member who blocked the speaker -- or whom the
speaker blocked -- still received the speaker's voice state and speaking frames
merely for sharing a voice channel. The sibling typing/presence resolver applies
that gate; this is the voice equivalent.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.api as api
from src.api.websocket.handlers.voice import VoiceHandler
from src.api.websocket.opcodes import GatewayOpcode

ACTOR = 12345
BLOCKER = 777  # blocked the actor
BLOCKED = 888  # blocked BY the actor
NEUTRAL = 999
CHANNEL = 55
SERVER = 7

pytestmark = pytest.mark.unit


class _Relationships:
    def __init__(self, blocked, *, broken=False):
        self._blocked = blocked
        self._broken = broken

    def get_all_blocked_ids(self, user_id):
        if self._broken:
            raise RuntimeError("relationship store unavailable")
        return list(self._blocked.get(int(user_id), ()))


def _servers_double():
    """Voice channel every server member can view."""
    servers = MagicMock()
    servers.get_channel.return_value = SimpleNamespace(server_id=SERVER)
    servers.get_member_user_ids.side_effect = lambda server_id, exclude_user_id=None: [
        uid for uid in [ACTOR, BLOCKER, BLOCKED, NEUTRAL] if uid != exclude_user_id
    ]
    return servers


def _wire(monkeypatch, relationships):
    monkeypatch.setattr(api, "get_relationships", lambda: relationships)
    monkeypatch.setattr("src.api.websocket.is_setup", lambda: True)
    import src.core.voice as voice
    import src.core.voice.signaling as signaling

    monkeypatch.setattr(
        voice,
        "get_voice_state",
        lambda user_id: SimpleNamespace(
            channel_id=CHANNEL, self_mute=False, self_deaf=False
        ),
    )
    monkeypatch.setattr(signaling, "record_activity", lambda user_id: None)


@pytest.fixture
def dispatcher():
    d = MagicMock()
    d.dispatch_event = AsyncMock(return_value=1)
    d.dispatch_raw_to_users = AsyncMock(return_value=1)
    return d


@pytest.fixture
def relationships():
    # ``get_all_blocked_ids`` is the authoritative two-way set: both users who
    # blocked the actor and users the actor blocked are in this single list.
    return _Relationships({ACTOR: [BLOCKER, BLOCKED]})


def test_voice_recipient_resolver_drops_both_block_directions(
    monkeypatch, relationships
):
    """Non-vacuous: neutral viewer and the actor survive, both block halves go."""
    _wire(monkeypatch, relationships)
    handler = VoiceHandler(servers_module=_servers_double())

    # include_self=True is the voice-state path; the actor must echo to itself,
    # but neither blocked direction may be in the audience.
    assert handler._get_typing_recipient_ids(
        ACTOR, CHANNEL, SERVER, include_self=True
    ) == [ACTOR, NEUTRAL]
    # include_self=False is the speaking path.
    assert handler._get_typing_recipient_ids(
        ACTOR, CHANNEL, SERVER, include_self=False
    ) == [NEUTRAL]


@pytest.mark.asyncio
async def test_voice_state_and_speaking_dispatches_exclude_blocked(
    monkeypatch, relationships, dispatcher, authenticated_connection
):
    """The gate survives onto both explicit audiences handed to the dispatcher."""
    _wire(monkeypatch, relationships)
    monkeypatch.setattr("src.api.websocket.get_dispatcher", lambda: dispatcher)
    handler = VoiceHandler(servers_module=_servers_double())

    await handler.handle_voice_speaking(
        authenticated_connection,
        {"channel_id": CHANNEL, "speaking": True, "volume": 0.25},
    )

    # VOICE_STATE_UPDATE echoes to the actor and the neutral viewer only.
    dispatcher.dispatch_event.assert_awaited_once()
    state_event, state_targets = dispatcher.dispatch_event.await_args.args
    assert state_event.event_type.value == "VOICE_STATE_UPDATE"
    assert sorted(state_targets) == sorted([ACTOR, NEUTRAL])

    # VOICE_SPEAKING reaches only the neutral viewer (the sender is excluded).
    dispatcher.dispatch_raw_to_users.assert_awaited_once()
    speaking_targets, opcode, _ = dispatcher.dispatch_raw_to_users.await_args.args
    assert sorted(speaking_targets) == [NEUTRAL]
    assert int(opcode) == int(GatewayOpcode.VOICE_SPEAKING)


@pytest.mark.asyncio
async def test_voice_audience_fails_closed_when_block_set_unavailable(
    monkeypatch, dispatcher, authenticated_connection
):
    """An authoritative-lookup failure must not widen a voice audience."""
    _wire(monkeypatch, _Relationships({}, broken=True))
    monkeypatch.setattr("src.api.websocket.get_dispatcher", lambda: dispatcher)
    handler = VoiceHandler(servers_module=_servers_double())

    await handler.handle_voice_speaking(
        authenticated_connection,
        {"channel_id": CHANNEL, "speaking": True, "volume": 0.25},
    )

    dispatcher.dispatch_event.assert_not_awaited()
    dispatcher.dispatch_raw_to_users.assert_not_awaited()
