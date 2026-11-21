"""VOICE_SPEAKING resolves the channel audience once per frame, not twice.

A speaking frame is high-frequency keepalive traffic. Both the voice-state
event and the speaking indicator target the *same* channel members, resolved
through ``_get_typing_recipient_ids`` (one ``get_channel`` visibility check per
member). Before the fix each of the two dispatch helpers resolved that audience
independently, so one frame paid ~2N per-member visibility checks plus two
server-id lookups. The audience is now resolved once per frame and shared.

Recipients are still derived through the same visibility filter, keyed by the
same (user, channel, server); the sender is removed for the speaking indicator
only. A channel the caller cannot resolve has no audience (fail closed).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.websocket.handlers.voice import VoiceHandler
from src.api.websocket.opcodes import GatewayOpcode


class CountingServers:
    """Servers double that counts the per-member visibility checks."""

    def __init__(self, members, server_id=7):
        self.server_id = server_id
        self.members = list(members)
        self.get_channel_calls = 0

    def get_channel(self, channel_id, user_id):
        self.get_channel_calls += 1
        return SimpleNamespace(server_id=self.server_id)

    def get_member_user_ids(self, server_id, exclude_user_id=None):
        return [m for m in self.members if m != exclude_user_id]


def _wire(monkeypatch, dispatcher_double):
    monkeypatch.setattr("src.api.websocket.is_setup", lambda: True)
    monkeypatch.setattr("src.api.websocket.get_dispatcher", lambda: dispatcher_double)
    import src.core.voice as voice
    import src.core.voice.signaling as signaling

    monkeypatch.setattr(
        voice,
        "get_voice_state",
        lambda user_id: SimpleNamespace(
            channel_id=55, self_mute=False, self_deaf=False
        ),
    )
    monkeypatch.setattr(signaling, "record_activity", lambda user_id: None)


@pytest.mark.asyncio
async def test_speaking_resolves_the_channel_audience_once(
    monkeypatch, authenticated_connection
) -> None:
    dispatcher_double = MagicMock()
    dispatcher_double.dispatch_raw_to_users = AsyncMock(return_value=1)
    dispatcher_double.dispatch_event = AsyncMock(return_value=1)
    _wire(monkeypatch, dispatcher_double)

    # Sender + four other visible members -> N = 5.
    servers = CountingServers(members=[12345, 777, 888, 999, 1010])
    handler = VoiceHandler(servers_module=servers)

    result = await handler.handle_voice_speaking(
        authenticated_connection,
        {"channel_id": 55, "speaking": True, "volume": 0.4},
    )

    assert result == (None, None, None)

    # Both dispatches still happen with the correct, non-widened audiences:
    # the speaking indicator excludes the sender; the state event includes it.
    targets, opcode, payload = dispatcher_double.dispatch_raw_to_users.await_args.args
    assert int(opcode) == int(GatewayOpcode.VOICE_SPEAKING)
    assert sorted(targets) == [777, 888, 999, 1010]
    assert payload == {
        "user_id": "12345",
        "channel_id": "55",
        "speaking": True,
        "volume": 0.4,
    }

    event, event_targets = dispatcher_double.dispatch_event.await_args.args
    assert sorted(event_targets) == [777, 888, 999, 1010, 12345]

    # One server-id lookup + one visibility check per member. Previously the
    # audience was resolved twice (2 lookups + 2N visibility checks).
    assert servers.get_channel_calls == len(servers.members) + 1, (
        "speaking frame re-resolved the channel audience; "
        f"get_channel called {servers.get_channel_calls} times"
    )


@pytest.mark.asyncio
async def test_speaking_unresolvable_channel_has_no_audience(
    monkeypatch, authenticated_connection
) -> None:
    """A channel the caller cannot resolve fails closed for both dispatches."""
    dispatcher_double = MagicMock()
    dispatcher_double.dispatch_raw_to_users = AsyncMock(return_value=1)
    dispatcher_double.dispatch_event = AsyncMock(return_value=1)
    _wire(monkeypatch, dispatcher_double)

    servers = MagicMock()
    servers.get_channel.return_value = None
    handler = VoiceHandler(servers_module=servers)

    await handler.handle_voice_speaking(
        authenticated_connection,
        {"channel_id": 55, "speaking": True, "volume": 1},
    )

    dispatcher_double.dispatch_raw_to_users.assert_not_awaited()
    dispatcher_double.dispatch_event.assert_not_awaited()
