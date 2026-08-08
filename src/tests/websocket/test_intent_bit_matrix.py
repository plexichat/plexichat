"""Dense matrix tests for gateway intent bit math and validation."""

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
from src.core.events.types import GatewayIntent

N_BITS = 22


# ---------------------------------------------------------------------------
# Single-bit validity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bit", range(N_BITS))
def test_every_single_bit_valid(bit):
    assert validate_intents(1 << bit) is True


@pytest.mark.parametrize("bit", range(N_BITS))
def test_every_single_bit_has_description(bit):
    desc = get_intent_description(GatewayIntent(1 << bit))
    assert desc != "Unknown intent"
    assert len(desc) > 5


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------


def test_all_intents_is_exact_bitmask():
    assert ALL_INTENTS == (1 << N_BITS) - 1


@pytest.mark.parametrize("value", [ALL_INTENTS, 0, 1, ALL_INTENTS - 1])
def test_in_range_values_valid(value):
    assert validate_intents(value) is True


@pytest.mark.parametrize(
    "value",
    [
        ALL_INTENTS + 1,
        ALL_INTENTS + 4096,
        1 << N_BITS,
        1 << 30,
        -1,
        -100,
        True,
        False,
        "1",
        1.0,
        None,
        [],
        {},
        (1,),
    ],
)
def test_out_of_range_or_non_int_invalid(value):
    assert validate_intents(value) is False


# ---------------------------------------------------------------------------
# Privileged intents
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bit", "expected"),
    [
        (0, False),  # GUILDS
        (1, True),  # GUILD_MEMBERS (privileged)
        (2, False),  # GUILD_BANS
        (3, False),  # GUILD_EMOJIS
        (7, False),  # GUILD_VOICE_STATES
        (8, True),  # GUILD_PRESENCES (privileged)
        (9, False),  # GUILD_MESSAGES
        (15, True),  # MESSAGE_CONTENT (privileged)
        (16, False),  # SCHEDULED_EVENTS
        (20, False),  # ARTIFACTS
        (21, False),  # SUMS
    ],
)
def test_privileged_single_bits(bit, expected):
    assert has_privileged_intents(1 << bit) is expected


@pytest.mark.parametrize(
    "intent",
    [
        GatewayIntent.GUILD_MEMBERS,
        GatewayIntent.GUILD_PRESENCES,
        GatewayIntent.MESSAGE_CONTENT,
    ],
)
def test_privileged_intents_flagged(intent):
    assert has_privileged_intents(int(intent)) is True


@pytest.mark.parametrize(
    "intent",
    [
        GatewayIntent.GUILDS,
        GatewayIntent.GUILD_MESSAGES,
        GatewayIntent.DIRECT_MESSAGES,
        GatewayIntent.ARTIFACTS,
        GatewayIntent.SUMS,
        GatewayIntent.SCHEDULED_EVENTS,
        GatewayIntent.AUDIT_LOG,
        GatewayIntent.AUTOMOD,
        GatewayIntent.THREADS,
    ],
)
def test_non_privileged_intents_not_flagged(intent):
    assert has_privileged_intents(int(intent)) is False


def test_privileged_constant_exact():
    expected = (
        int(GatewayIntent.GUILD_MEMBERS)
        | int(GatewayIntent.GUILD_PRESENCES)
        | int(GatewayIntent.MESSAGE_CONTENT)
    )
    assert PRIVILEGED_INTENTS == expected


def test_all_intents_include_privileged():
    assert has_privileged_intents(ALL_INTENTS) is True


def test_zero_intents_not_privileged():
    assert has_privileged_intents(0) is False


# ---------------------------------------------------------------------------
# get_privileged_intents_requested
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("intents", "expected"),
    [
        (int(GatewayIntent.GUILD_MEMBERS), ["GUILD_MEMBERS"]),
        (int(GatewayIntent.GUILD_PRESENCES), ["GUILD_PRESENCES"]),
        (int(GatewayIntent.MESSAGE_CONTENT), ["MESSAGE_CONTENT"]),
        (
            int(GatewayIntent.GUILD_MEMBERS | GatewayIntent.GUILD_PRESENCES),
            ["GUILD_MEMBERS", "GUILD_PRESENCES"],
        ),
        (
            int(GatewayIntent.GUILD_MEMBERS | GatewayIntent.MESSAGE_CONTENT),
            ["GUILD_MEMBERS", "MESSAGE_CONTENT"],
        ),
        (int(GatewayIntent.GUILDS), []),
        (0, []),
        (int(GatewayIntent.ARTIFACTS), []),
    ],
)
def test_privileged_requested_names(intents, expected):
    assert get_privileged_intents_requested(intents) == expected


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
        (int(GatewayIntent.ARTIFACTS), True, False),
        (0, False, False),
        (0, True, False),
        (ALL_INTENTS, True, True),
        (ALL_INTENTS, False, True),
    ],
)
def test_should_include_content_matrix(intents, is_dm, expected):
    assert should_include_message_content(intents, is_dm) is expected


# ---------------------------------------------------------------------------
# Default intents
# ---------------------------------------------------------------------------


def test_default_intents_subset_of_all():
    assert isinstance(DEFAULT_INTENTS, int)
    assert 0 <= DEFAULT_INTENTS <= ALL_INTENTS
    assert validate_intents(DEFAULT_INTENTS) is True
