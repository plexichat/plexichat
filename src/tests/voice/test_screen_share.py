"""
Tests for screen sharing signaling.
"""

import pytest

from src.core.voice.signaling import (
    setup,
    create_voice_connection,
    start_screen_share,
    stop_screen_share,
    disconnect_voice,
    NotConnectedError,
    ScreenShareError,
)
from src.core.voice.signaling.models import ScreenShareState


@pytest.fixture(scope="module")
def signaling_setup(db_and_modules):
    """Setup signaling module for tests."""
    db, auth, servers, relationships, presence, voice = db_and_modules

    setup(
        voice_module=voice,
        events_module=None,
        sfu_backend="mediasoup",
        stun_urls=["stun:stun.l.google.com:19302"],
        turn_urls=["turn:turn.example.com:3478"],
        turn_secret="test_secret_key_for_turn",
        turn_ttl=3600,
    )

    return voice


class TestScreenShare:
    """Tests for screen sharing functionality."""

    def test_start_screen_share(self, server_with_voice, signaling_setup):
        """Test starting screen share."""
        (
            owner,
            member1,
            member2,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_voice

        create_voice_connection(member1.id, voice_channel.id)

        state = start_screen_share(member1.id, voice_channel.id)

        assert state is not None
        assert isinstance(state, ScreenShareState)
        assert state.user_id == member1.id
        assert state.channel_id == voice_channel.id
        assert state.active is True
        assert state.stream_id is not None
        assert state.started_at is not None

        # Cleanup
        disconnect_voice(member1.id)

    def test_stop_screen_share(self, server_with_voice, signaling_setup):
        """Test stopping screen share."""
        (
            owner,
            member1,
            member2,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_voice

        create_voice_connection(member1.id, voice_channel.id)
        start_screen_share(member1.id, voice_channel.id)

        result = stop_screen_share(member1.id, voice_channel.id)

        assert result is True

        # Cleanup
        disconnect_voice(member1.id)

    def test_start_screen_share_not_connected(self, server_with_voice, signaling_setup):
        """Test starting screen share when not connected."""
        (
            owner,
            member1,
            member2,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_voice

        with pytest.raises(NotConnectedError):
            start_screen_share(999999, voice_channel.id)

    def test_start_screen_share_wrong_channel(self, server_with_voice, signaling_setup):
        """Test starting screen share in wrong channel."""
        (
            owner,
            member1,
            member2,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_voice

        create_voice_connection(member1.id, voice_channel.id)

        with pytest.raises(ScreenShareError) as exc_info:
            start_screen_share(member1.id, 999999)

        assert exc_info.value.reason == "channel_mismatch"

        # Cleanup
        disconnect_voice(member1.id)

    def test_start_screen_share_already_sharing(
        self, server_with_voice, signaling_setup
    ):
        """Test starting screen share when already sharing."""
        (
            owner,
            member1,
            member2,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_voice

        create_voice_connection(member2.id, voice_channel.id)
        start_screen_share(member2.id, voice_channel.id)

        with pytest.raises(ScreenShareError) as exc_info:
            start_screen_share(member2.id, voice_channel.id)

        assert exc_info.value.reason == "already_sharing"

        # Cleanup
        disconnect_voice(member2.id)

    def test_stop_screen_share_not_sharing(self, server_with_voice, signaling_setup):
        """Test stopping screen share when not sharing."""
        (
            owner,
            member1,
            member2,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_voice

        create_voice_connection(member1.id, voice_channel.id)

        result = stop_screen_share(member1.id, voice_channel.id)

        assert result is False

        # Cleanup
        disconnect_voice(member1.id)

    def test_stop_screen_share_not_connected(self, server_with_voice, signaling_setup):
        """Test stopping screen share when not connected."""
        (
            owner,
            member1,
            member2,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_voice

        result = stop_screen_share(999999, voice_channel.id)

        assert result is False

    def test_screen_share_state_to_dict(self, server_with_voice, signaling_setup):
        """Test ScreenShareState serialization."""
        (
            owner,
            member1,
            member2,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_voice

        create_voice_connection(member1.id, voice_channel.id)
        state = start_screen_share(member1.id, voice_channel.id)

        data = state.to_dict()

        assert "user_id" in data
        assert "channel_id" in data
        assert "active" in data
        assert "stream_id" in data
        assert "started_at" in data
        assert data["active"] is True

        # Cleanup
        disconnect_voice(member1.id)

    def test_multiple_users_screen_share(self, server_with_voice, signaling_setup):
        """Test multiple users can screen share in same channel."""
        (
            owner,
            member1,
            member2,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_voice

        create_voice_connection(member1.id, voice_channel.id)
        create_voice_connection(member2.id, voice_channel.id)

        state1 = start_screen_share(member1.id, voice_channel.id)
        state2 = start_screen_share(member2.id, voice_channel.id)

        assert state1.stream_id != state2.stream_id
        assert state1.active is True
        assert state2.active is True

        # Cleanup
        disconnect_voice(member1.id)
        disconnect_voice(member2.id)

    def test_screen_share_disconnect_cleans_up(
        self, server_with_voice, signaling_setup
    ):
        """Test that disconnecting cleans up screen share."""
        (
            owner,
            member1,
            member2,
            server,
            voice_channel,
            stage_channel,
            servers,
            voice,
        ) = server_with_voice

        create_voice_connection(owner.id, voice_channel.id)
        start_screen_share(owner.id, voice_channel.id)

        disconnect_voice(owner.id)

        # Should be able to connect and share again
        create_voice_connection(owner.id, voice_channel.id)
        state = start_screen_share(owner.id, voice_channel.id)

        assert state.active is True

        # Cleanup
        disconnect_voice(owner.id)
