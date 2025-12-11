"""
Tests for voice signaling module.
"""

import pytest
import time

from src.core.voice.signaling import (
    setup,
    get_voice_server_info,
    create_voice_connection,
    disconnect_voice,
    get_turn_credentials,
    get_connection_quality,
    NotConnectedError,
    AlreadyConnectedError,
)


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


class TestVoiceServerInfo:
    """Tests for get_voice_server_info."""

    def test_get_voice_server_info(self, server_with_voice, signaling_setup):
        """Test getting voice server info."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        info = get_voice_server_info(member1.id, voice_channel.id)

        assert info is not None
        assert info.user_id == member1.id
        assert info.channel_id == voice_channel.id
        assert info.session_id is not None
        assert info.endpoint is not None
        assert info.token is not None
        assert len(info.ice_servers) > 0

    def test_voice_server_info_includes_stun(self, server_with_voice, signaling_setup):
        """Test that STUN servers are included."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        info = get_voice_server_info(member1.id, voice_channel.id)

        stun_servers = [s for s in info.ice_servers if any("stun:" in u for u in s.urls)]
        assert len(stun_servers) > 0

    def test_voice_server_info_includes_turn(self, server_with_voice, signaling_setup):
        """Test that TURN servers are included with credentials."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        info = get_voice_server_info(member1.id, voice_channel.id)

        turn_servers = [s for s in info.ice_servers if any("turn:" in u for u in s.urls)]
        assert len(turn_servers) > 0

        turn_server = turn_servers[0]
        assert turn_server.username is not None
        assert turn_server.credential is not None


class TestVoiceConnection:
    """Tests for voice connection management."""

    def test_create_voice_connection(self, server_with_voice, signaling_setup):
        """Test creating a voice connection."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        info = create_voice_connection(member1.id, voice_channel.id)

        assert info is not None
        assert info.user_id == member1.id
        assert info.channel_id == voice_channel.id

        # Cleanup
        disconnect_voice(member1.id)

    def test_create_duplicate_connection_fails(self, server_with_voice, signaling_setup):
        """Test that creating duplicate connection raises error."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        create_voice_connection(member2.id, voice_channel.id)

        with pytest.raises(AlreadyConnectedError):
            create_voice_connection(member2.id, voice_channel.id)

        # Cleanup
        disconnect_voice(member2.id)

    def test_disconnect_voice(self, server_with_voice, signaling_setup):
        """Test disconnecting from voice."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        create_voice_connection(owner.id, voice_channel.id)

        result = disconnect_voice(owner.id)
        assert result is True

        # Should be able to connect again
        info = create_voice_connection(owner.id, voice_channel.id)
        assert info is not None

        disconnect_voice(owner.id)

    def test_disconnect_nonexistent_connection(self, server_with_voice, signaling_setup):
        """Test disconnecting when not connected."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        result = disconnect_voice(999999)
        assert result is False


class TestTURNCredentials:
    """Tests for TURN credential generation."""

    def test_get_turn_credentials(self, server_with_voice, signaling_setup):
        """Test getting TURN credentials."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        creds = get_turn_credentials(member1.id)

        assert creds is not None
        assert creds.username is not None
        assert creds.credential is not None
        assert len(creds.urls) > 0
        assert creds.ttl > 0
        assert creds.expires_at > time.time()

    def test_turn_credentials_unique_per_user(self, server_with_voice, signaling_setup):
        """Test that TURN credentials are unique per user."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        creds1 = get_turn_credentials(member1.id)
        creds2 = get_turn_credentials(member2.id)

        assert creds1.username != creds2.username
        assert creds1.credential != creds2.credential


class TestConnectionQuality:
    """Tests for connection quality monitoring."""

    def test_get_connection_quality(self, server_with_voice, signaling_setup):
        """Test getting connection quality."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        create_voice_connection(member1.id, voice_channel.id)

        quality = get_connection_quality(member1.id, voice_channel.id)

        assert quality is not None
        assert quality.user_id == member1.id
        assert quality.channel_id == voice_channel.id
        assert quality.quality_level is not None
        assert quality.bitrate > 0

        disconnect_voice(member1.id)

    def test_get_quality_not_connected_fails(self, server_with_voice, signaling_setup):
        """Test getting quality when not connected raises error."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        with pytest.raises(NotConnectedError):
            get_connection_quality(999999, voice_channel.id)
