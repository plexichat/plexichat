"""Voice gateway channel ids must pass the canonical snowflake gate.

The voice opcodes used to coerce the wire value with bare ``int()``. That
grammar accepts Unicode digit homoglyphs, PEP 515 separators and explicit
signs, so a token one hop reads as one id can resolve to a different channel;
CONNECT and STATE_UPDATE also accepted negatives and oversized values that
then flowed into repository lookups and cache keys. Every other gateway
handler routes ids through ``parse_snowflake``. These tests pin that the voice
handlers use the same gate and preserve exact 64-bit values.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.api.websocket.handlers.voice import VoiceHandler, _parse_channel_id
from src.api.websocket.opcodes import GatewayCloseCode


DECODE_ERROR = int(GatewayCloseCode.DECODE_ERROR)
BIG = 9007199254740993
BIG_STR = "9007199254740993"


def _authed(user_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        is_authenticated=True,
        user_id=user_id,
        connection_id="conn-1",
    )


@pytest.mark.parametrize(
    "malformed",
    [
        "٤٢",  # Arabic-Indic digits: int() -> 42
        "４２",  # fullwidth digits: int() -> 42
        "1_0",  # PEP 515 separator: int() -> 10
        "-1",  # negative id
        "0",  # below the snowflake floor
        "99999999999999999999",  # past the snowflake ceiling
        True,  # bool is not an id
        "",  # empty
    ],
)
def test_parse_channel_id_rejects_non_canonical(malformed) -> None:
    assert _parse_channel_id(malformed) is None


def test_parse_channel_id_preserves_exact_64bit_value() -> None:
    assert _parse_channel_id(BIG_STR) == BIG
    assert _parse_channel_id(BIG) == BIG


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed",
    ["٤٢", "1_0", "-1", "0", "99999999999999999999"],
)
async def test_voice_connect_rejects_malformed_channel_id(
    monkeypatch, malformed
) -> None:
    """A malformed id is refused before any voice/signaling lookup."""
    import src.core.voice as voice

    def _must_not_lookup(*args, **kwargs):
        raise AssertionError("malformed channel id reached join_channel")

    monkeypatch.setattr(voice, "join_channel", _must_not_lookup, raising=False)

    result = await VoiceHandler().handle_voice_connect(
        _authed(), {"channel_id": malformed}
    )
    assert result == (None, None, DECODE_ERROR)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed",
    ["٤٢", "1_0", "-1", "0", "99999999999999999999"],
)
async def test_voice_state_update_rejects_malformed_channel_id(malformed) -> None:
    result = await VoiceHandler().handle_voice_state_update(
        _authed(), {"channel_id": malformed}
    )
    assert result == (None, None, DECODE_ERROR)
