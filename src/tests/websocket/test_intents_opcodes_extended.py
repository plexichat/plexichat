"""Extended targeted tests for gateway intents validation and opcode/close-code tables."""

import pytest

from src.api.websocket.intents import (
    validate_intents,
    has_privileged_intents,
    get_privileged_intents_requested,
    should_include_message_content,
    get_intent_description,
    ALL_INTENTS,
    PRIVILEGED_INTENTS,
    DEFAULT_INTENTS,
)
from src.api.websocket.opcodes import (
    GatewayOpcode,
    GatewayCloseCode,
    is_resumable,
    get_close_message,
    CLOSE_CODE_MESSAGES,
)
from src.core.events.types import GatewayIntent


# ---------------------------------------------------------------------------
# validate_intents
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "intents",
    [
        0,
        1,
        int(GatewayIntent.GUILDS),
        int(GatewayIntent.GUILD_MESSAGES | GatewayIntent.DIRECT_MESSAGES),
        ALL_INTENTS,
        ALL_INTENTS,
        int(GatewayIntent.SUMS) | int(GatewayIntent.ARTIFACTS),
    ],
)
def test_valid_intents_accepted(intents):
    assert validate_intents(intents) is True


@pytest.mark.parametrize(
    "intents",
    [
        -1,
        -100,
        ALL_INTENTS + 1,
        ALL_INTENTS + 4096,
        1 << 30,
        True,  # bool is an int subclass but never a valid mask
        False,
        "1",  # strings rejected
        1.0,  # floats rejected
        None,
        [1, 2],
        {"intents": 1},
        (1,),
    ],
)
def test_invalid_intents_rejected(intents):
    assert validate_intents(intents) is False


def test_every_single_intent_bit_valid():
    for bit in range(22):
        assert validate_intents(1 << bit) is True


def test_every_intent_combo_of_two_valid():
    members = list(GatewayIntent)
    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            combo = int(members[i]) | int(members[j])
            assert validate_intents(combo) is True


# ---------------------------------------------------------------------------
# Privileged intents
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("intents", "expected"),
    [
        (int(GatewayIntent.GUILD_MEMBERS), True),
        (int(GatewayIntent.GUILD_PRESENCES), True),
        (int(GatewayIntent.MESSAGE_CONTENT), True),
        (int(GatewayIntent.GUILD_MEMBERS | GatewayIntent.GUILDS), True),
        (int(GatewayIntent.GUILDS), False),
        (int(GatewayIntent.GUILD_MESSAGES), False),
        (0, False),
        (ALL_INTENTS, True),
    ],
)
def test_has_privileged_intents(intents, expected):
    assert has_privileged_intents(intents) is expected


def test_privileged_intents_constant_contains_three():
    expected = (
        int(GatewayIntent.GUILD_MEMBERS)
        | int(GatewayIntent.GUILD_PRESENCES)
        | int(GatewayIntent.MESSAGE_CONTENT)
    )
    assert PRIVILEGED_INTENTS == expected


def test_get_privileged_intents_requested_names():
    req = get_privileged_intents_requested(
        int(GatewayIntent.GUILD_MEMBERS) | int(GatewayIntent.MESSAGE_CONTENT)
    )
    assert set(req) == {"GUILD_MEMBERS", "MESSAGE_CONTENT"}


def test_get_privileged_intents_requested_empty():
    assert get_privileged_intents_requested(int(GatewayIntent.GUILDS)) == []


def test_default_intents_non_empty_and_valid():
    assert isinstance(DEFAULT_INTENTS, int)
    # default_intents() ORs several non-zero intent bits together
    assert DEFAULT_INTENTS > 0
    assert validate_intents(DEFAULT_INTENTS) is True
    assert has_privileged_intents(DEFAULT_INTENTS) is False


# ---------------------------------------------------------------------------
# should_include_message_content
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("intents", "is_dm", "expected"),
    [
        (int(GatewayIntent.MESSAGE_CONTENT), False, True),
        (int(GatewayIntent.MESSAGE_CONTENT), True, False),
        (int(GatewayIntent.DIRECT_MESSAGES), True, True),
        (int(GatewayIntent.DIRECT_MESSAGES), False, False),
        (int(GatewayIntent.GUILD_MESSAGES), False, False),
        (
            int(GatewayIntent.MESSAGE_CONTENT | GatewayIntent.DIRECT_MESSAGES),
            True,
            True,
        ),
        (
            int(GatewayIntent.MESSAGE_CONTENT | GatewayIntent.DIRECT_MESSAGES),
            False,
            True,
        ),
        (0, False, False),
        (0, True, False),
    ],
)
def test_should_include_message_content(intents, is_dm, expected):
    assert should_include_message_content(intents, is_dm) is expected


# ---------------------------------------------------------------------------
# Intent descriptions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "intent",
    list(GatewayIntent),
)
def test_all_intents_have_descriptions(intent):
    desc = get_intent_description(intent)
    assert desc != "Unknown intent"
    assert isinstance(desc, str)
    assert len(desc) > 5


def test_unknown_intent_description():
    assert get_intent_description("NOT_AN_INTENT") == "Unknown intent"


# ---------------------------------------------------------------------------
# Opcodes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DISPATCH", 0),
        ("HEARTBEAT", 1),
        ("IDENTIFY", 2),
        ("PRESENCE_UPDATE", 3),
        ("VOICE_STATE_UPDATE", 4),
        ("RESUME", 6),
        ("RECONNECT", 7),
        ("REQUEST_GUILD_MEMBERS", 8),
        ("INVALID_SESSION", 9),
        ("HELLO", 10),
        ("HEARTBEAT_ACK", 11),
        ("SERVER_STATUS", 12),
        ("VERSION_CHECK", 13),
        ("VOICE_CONNECT", 20),
        ("VOICE_DISCONNECT", 21),
        ("VOICE_SDP_OFFER", 22),
        ("VOICE_SDP_ANSWER", 23),
        ("VOICE_ICE_CANDIDATE", 24),
        ("VOICE_SPEAKING", 25),
        ("VOICE_QUALITY", 26),
        ("INTERACTION_CREATE", 30),
        ("INTERACTION_RESPONSE", 31),
        ("TYPING_START", 40),
        ("TYPING_STOP", 41),
        ("RATCHET_UPDATE", 50),
        ("ARTIFACT_SUBSCRIBE", 60),
        ("ARTIFACT_UNSUBSCRIBE", 61),
        ("ARTIFACT_OP", 62),
        ("ARTIFACT_SYNC", 63),
        ("SUM_SUBSCRIBE", 64),
        ("SUM_UNSUBSCRIBE", 65),
        ("SUM_OP", 66),
        ("SUM_SYNC", 67),
    ],
)
def test_opcode_values(name, value):
    assert GatewayOpcode[name].value == value


def test_all_opcodes_unique():
    values = [op.value for op in GatewayOpcode]
    assert len(values) == len(set(values))


@pytest.mark.asyncio
async def test_invalid_session_payload_uses_gateway_wire_shape():
    """The gateway reads the resumable flag from the nested ``d`` payload."""
    from unittest.mock import AsyncMock

    from src.api.websocket.dispatcher import GatewayDispatcher

    dispatcher = GatewayDispatcher(session_manager=object())
    connection = type("ConnectionStub", (), {})()
    connection.send_json = AsyncMock(return_value=True)
    connection.increment_sequence = lambda: 1
    connection.session_id = None

    assert await dispatcher.send_invalid_session(connection, resumable=True)
    sent = connection.send_json.await_args.args[0]
    assert sent == {"op": int(GatewayOpcode.INVALID_SESSION), "d": True}


# ---------------------------------------------------------------------------
# Close codes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("UNKNOWN_ERROR", 4000),
        ("UNKNOWN_OPCODE", 4001),
        ("DECODE_ERROR", 4002),
        ("NOT_AUTHENTICATED", 4003),
        ("AUTHENTICATION_FAILED", 4004),
        ("ALREADY_AUTHENTICATED", 4005),
        ("INVALID_SEQ", 4007),
        ("RATE_LIMITED", 4008),
        ("SESSION_TIMED_OUT", 4009),
        ("INVALID_SHARD", 4010),
        ("SHARDING_REQUIRED", 4011),
        ("INVALID_API_VERSION", 4012),
        ("INVALID_INTENTS", 4013),
        ("DISALLOWED_INTENTS", 4014),
        ("VERSION_OUTDATED", 4015),
        ("SERVER_MAINTENANCE", 4016),
        ("SERVER_SHUTDOWN", 4017),
        ("NORMAL_CLOSURE", 1000),
        ("GOING_AWAY", 1001),
    ],
)
def test_close_code_values(name, value):
    assert GatewayCloseCode[name].value == value


@pytest.mark.parametrize("code", list(GatewayCloseCode))
def test_every_close_code_has_message(code):
    msg = get_close_message(int(code))
    assert msg != "Unknown close code"
    assert len(msg) > 0


@pytest.mark.parametrize(
    "code", [0, 999, 1002, 3999, 4018, 5000, -1, None, "4000", 3.5]
)
def test_unknown_close_code_message(code):
    assert get_close_message(code) == "Unknown close code"


def test_close_code_messages_cover_all_codes():
    for code in GatewayCloseCode:
        assert code in CLOSE_CODE_MESSAGES


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (GatewayCloseCode.UNKNOWN_ERROR, True),
        (GatewayCloseCode.UNKNOWN_OPCODE, True),
        (GatewayCloseCode.DECODE_ERROR, True),
        (GatewayCloseCode.NOT_AUTHENTICATED, True),
        (GatewayCloseCode.ALREADY_AUTHENTICATED, True),
        (GatewayCloseCode.INVALID_SEQ, True),
        (GatewayCloseCode.RATE_LIMITED, True),
        (GatewayCloseCode.SESSION_TIMED_OUT, True),
        (GatewayCloseCode.AUTHENTICATION_FAILED, False),
        (GatewayCloseCode.INVALID_INTENTS, False),
        (GatewayCloseCode.DISALLOWED_INTENTS, False),
        (GatewayCloseCode.VERSION_OUTDATED, False),
        (GatewayCloseCode.SERVER_MAINTENANCE, False),
        (GatewayCloseCode.SERVER_SHUTDOWN, False),
        (GatewayCloseCode.NORMAL_CLOSURE, False),
        (GatewayCloseCode.GOING_AWAY, False),
    ],
)
def test_is_resumable(code, expected):
    assert is_resumable(int(code)) is expected


def test_resumable_codes_all_exist_in_messages():
    for code in {int(c) for c in RESUMABLE_ALL()}:
        assert get_close_message(code) != "Unknown close code"


def RESUMABLE_ALL():
    from src.api.websocket.opcodes import RESUMABLE_CLOSE_CODES

    return RESUMABLE_CLOSE_CODES
