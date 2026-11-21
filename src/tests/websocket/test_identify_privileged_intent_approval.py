"""IDENTIFY must not grant privileged gateway intents without approval.

The gateway documents that ``GUILD_MEMBERS``, ``GUILD_PRESENCES`` and
``MESSAGE_CONTENT`` require approval and that asking for them without it must
close the socket with ``DISALLOWED_INTENTS`` (4014).  A client that can simply
set those bits in ``IDENTIFY`` widens its own event delivery to privileged
member/presence events, defeating the intent privilege model entirely.
"""

import pytest

from src.api.websocket.intents import ALL_INTENTS, DEFAULT_INTENTS, PRIVILEGED_INTENTS
from src.api.websocket.opcodes import GatewayCloseCode, GatewayOpcode
from src.core.events.types import GatewayIntent


def _identify_payload(intents):
    return {
        "token": "test_token",
        "intents": intents,
        "properties": {"os": "linux", "browser": "pytest", "device": "pytest"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "privileged",
    [
        int(GatewayIntent.GUILD_MEMBERS),
        int(GatewayIntent.GUILD_PRESENCES),
        int(GatewayIntent.MESSAGE_CONTENT),
        PRIVILEGED_INTENTS,
        ALL_INTENTS,
    ],
)
async def test_identify_rejects_unapproved_privileged_intents(
    opcode_handler, connection, privileged
):
    """Requesting a privileged bit must be denied with 4014, not accepted."""
    _, _, close_code = await opcode_handler.handle(
        connection,
        GatewayOpcode.IDENTIFY,
        _identify_payload(privileged),
    )
    assert close_code == int(GatewayCloseCode.DISALLOWED_INTENTS)
    # Denial must happen before identity/session creation: no session is left
    # behind that a RESUME could later use to regain the privileged mask.
    assert connection.is_authenticated is False
    assert connection.intents == 0


@pytest.mark.asyncio
async def test_identify_allows_non_privileged_intents(opcode_handler, connection):
    """The approval gate must not reject ordinary, non-privileged intents."""
    _, _, close_code = await opcode_handler.handle(
        connection,
        GatewayOpcode.IDENTIFY,
        _identify_payload(DEFAULT_INTENTS),
    )
    assert close_code is None
    assert connection.is_authenticated is True
    assert connection.intents == DEFAULT_INTENTS
