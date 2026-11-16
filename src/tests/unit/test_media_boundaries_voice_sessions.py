"""Voice session ids, SFU ids and participant fields are bounded."""

import pytest

from src.core.voice.boundaries import (
    MAX_PARTICIPANTS_PER_CHANNEL,
    MAX_SESSION_ID_LENGTH,
    MAX_SFU_ID_LENGTH,
    clamp_participant_count,
    clamp_user_limit,
    validate_session_id,
    validate_sfu_identifier,
)
from src.core.voice.signaling.models import VoiceConnection, SignalingState


class TestSessionIds:
    def test_valid_session_id(self):
        assert validate_session_id("a" * MAX_SESSION_ID_LENGTH)

    @pytest.mark.parametrize(
        "value",
        ["a" * (MAX_SESSION_ID_LENGTH + 1), "x\x00y", "x\u200by", "x\u202ey"],
    )
    def test_invalid_session_id_rejected(self, value):
        with pytest.raises(ValueError):
            validate_session_id(value)

    def test_sfu_identifier_bounded(self):
        assert validate_sfu_identifier("room-1")
        with pytest.raises(ValueError):
            validate_sfu_identifier("x" * (MAX_SFU_ID_LENGTH + 1))


class TestParticipantFields:
    @pytest.mark.parametrize("value", [0, 1, 50, MAX_PARTICIPANTS_PER_CHANNEL, "10"])
    def test_valid_limits(self, value):
        assert clamp_user_limit(value) == int(value)

    @pytest.mark.parametrize("value", [-5, 10**9, "abc", None, 2.9])
    def test_invalid_limits_clamped(self, value):
        assert 0 <= clamp_user_limit(value) <= MAX_PARTICIPANTS_PER_CHANNEL

    def test_participant_count_clamped(self):
        assert clamp_participant_count(10**9) == MAX_PARTICIPANTS_PER_CHANNEL
        assert clamp_participant_count(-3) == 0
        assert clamp_participant_count("12") == 12


class TestSignalingModels:
    def test_voice_connection_bounds_ids(self):
        conn = VoiceConnection(
            user_id=1,
            channel_id=2,
            session_id="s1",
            state=SignalingState.NEW,
            created_at=0,
            last_activity=0,
            sfu_room_id="room-1",
            sfu_producer_ids=["p1", "p2"],
        )
        assert conn.session_id == "s1"
        assert conn.sfu_room_id == "room-1"
        assert conn.sfu_producer_ids == ["p1", "p2"]

    def test_voice_connection_rejects_oversized_session(self):
        with pytest.raises(ValueError):
            VoiceConnection(
                user_id=1,
                channel_id=2,
                session_id="s" * (MAX_SESSION_ID_LENGTH + 1),
                state=SignalingState.NEW,
                created_at=0,
                last_activity=0,
            )

    def test_voice_connection_rejects_oversized_sfu_id(self):
        with pytest.raises(ValueError):
            VoiceConnection(
                user_id=1,
                channel_id=2,
                session_id="s1",
                state=SignalingState.NEW,
                created_at=0,
                last_activity=0,
                sfu_room_id="r" * (MAX_SFU_ID_LENGTH + 1),
            )
