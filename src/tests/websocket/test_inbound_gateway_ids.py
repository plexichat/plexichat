"""Ratchet: inbound gateway handlers parse snowflake ids without loss.

The client sends ids as decimal strings (its outbound ratchet enforces that)
because a JSON number past ``2**53`` is already rounded by the browser before
the server ever sees it. The server side of the contract is therefore two-fold:
accept the decimal-string form, and turn it into an *exact* Python ``int``.

``int("9007199254740993")`` is exact, but a ``float()``/JSON-number hop is not
— it rounds to ``9007199254740992``. These tests drive each inbound opcode that
reads an id with the first non-representable value and assert the handler hands
its downstream the exact integer, so a future handler that reaches for
``float()`` (or a length-preserving coercion) is caught here.

``SimpleNamespace`` connections stand in for a real ``Connection`` exactly like
the other websocket handler tests (the handlers only touch attributes).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.api.websocket.handlers.artifacts import ArtifactHandler
from src.api.websocket.handlers.guild import GuildHandler
from src.api.websocket.handlers.presence import PresenceHandler
from src.api.websocket.handlers.sum import _workbook_id
from src.api.websocket.handlers.voice import VoiceHandler

# The first integer a JS ``Number`` cannot represent exactly: ``float`` rounds
# it to 9007199254740992, so a lossy hop changes the value by one.
BIG = 9007199254740993
BIG_STR = "9007199254740993"


def _authed(user_id: int = 1, connection_id: str = "conn-1") -> SimpleNamespace:
    return SimpleNamespace(
        is_authenticated=True,
        user_id=user_id,
        connection_id=connection_id,
    )


def test_sum_workbook_id_parses_decimal_string_exactly() -> None:
    """A string id round-trips to the exact int (and an int is a no-op)."""
    assert _workbook_id({"workbook_id": BIG_STR}) == BIG
    assert _workbook_id({"workbook_id": BIG}) == BIG


@pytest.mark.asyncio
async def test_voice_sdp_offer_parses_channel_id_exactly(monkeypatch) -> None:
    """VOICE_SDP_OFFER hands signaling the exact channel id from the wire."""
    import src.core.voice.signaling as signaling

    captured: dict = {}

    async def _answer(user_id, channel_id, sdp):
        captured["channel_id"] = channel_id
        return SimpleNamespace(sdp="v=0", sdp_type=SimpleNamespace(value="answer"))

    monkeypatch.setattr(signaling, "handle_sdp_offer_async", _answer)

    await VoiceHandler().handle_voice_sdp_offer(
        _authed(), {"channel_id": BIG_STR, "sdp": "v=0"}
    )

    assert captured["channel_id"] == BIG


@pytest.mark.asyncio
async def test_guild_request_members_parses_guild_id_exactly(monkeypatch) -> None:
    """REQUEST_GUILD_MEMBERS queries the servers module with the exact id."""
    import src.api.websocket as websocket_mod

    servers = Mock()
    servers.get_server.return_value = SimpleNamespace()
    servers.get_members.return_value = []

    dispatcher = Mock()
    dispatcher.dispatch_to_connection = AsyncMock(return_value=True)
    monkeypatch.setattr(websocket_mod, "get_dispatcher", lambda: dispatcher)

    await GuildHandler(servers_module=servers).handle_request_guild_members(
        _authed(), {"guild_id": BIG_STR}
    )

    assert servers.get_server.call_args[0][0] == BIG
    assert servers.get_members.call_args[0][1] == BIG


@pytest.mark.asyncio
async def test_typing_start_parses_channel_id_exactly(monkeypatch) -> None:
    """TYPING_START resolves recipients for the exact channel id."""
    import src.api.websocket.handlers.presence as presence_mod

    captured: dict = {}

    def _recipients(user_id, channel_id):
        captured["channel_id"] = channel_id
        return [], None

    monkeypatch.setattr(presence_mod, "_get_channel_recipients", _recipients)

    await PresenceHandler().handle_typing_start(_authed(), {"channel_id": BIG_STR})

    assert captured["channel_id"] == BIG


@pytest.mark.asyncio
async def test_artifact_unsubscribe_parses_artifact_id_exactly(monkeypatch) -> None:
    """ARTIFACT_UNSUBSCRIBE removes the exact subscription id."""
    handler = ArtifactHandler()
    captured: dict = {}

    def _unsubscribe(user_id, artifact_id, connection_id=None, *args, **kwargs):
        captured["artifact_id"] = artifact_id

    monkeypatch.setattr(handler._registry, "unsubscribe", _unsubscribe)

    await handler.handle_artifact_unsubscribe(_authed(), {"artifact_id": BIG_STR})

    assert captured["artifact_id"] == BIG
