"""
Tests for complete voice connection flow.
"""

import pytest

from src.core.voice.signaling import (
    setup,
    get_voice_server_info,
    create_voice_connection,
    handle_sdp_offer,
    handle_ice_candidate,
    disconnect_voice,
    get_connection_quality,
    NotConnectedError,
    AlreadyConnectedError,
)
from src.core.voice.signaling.models import SDPType


# Sample SDP offer for testing
SAMPLE_SDP_OFFER = """v=0
o=- 4611731400430051336 2 IN IP4 127.0.0.1
s=-
t=0 0
a=group:BUNDLE 0
a=ice-ufrag:abcd
a=ice-pwd:efghijklmnopqrstuvwxyz12
a=fingerprint:sha-256 AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99
a=setup:actpass
m=audio 9 UDP/TLS/RTP/SAVPF 111
c=IN IP4 0.0.0.0
a=rtcp:9 IN IP4 0.0.0.0
a=rtcp-mux
a=sendrecv
a=rtpmap:111 opus/48000/2
"""

SAMPLE_ICE_CANDIDATE = "candidate:1 1 udp 2130706431 192.168.1.100 54321 typ host"


@pytest.fixture(scope="function")
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


class TestVoiceConnectionFlow:
    """Tests for complete voice connection flow."""

    def test_full_connection_flow(self, server_with_voice, signaling_setup):
        """Test the complete voice connection flow."""
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

        # Step 1: Get voice server info
        info = get_voice_server_info(member1.id, voice_channel.id)
        assert info is not None
        assert info.session_id is not None

        # Step 2: Create voice connection
        conn_info = create_voice_connection(member1.id, voice_channel.id)
        assert conn_info is not None

        # Step 3: Send SDP offer and get answer
        answer = handle_sdp_offer(
            member1.id, voice_channel.id, SAMPLE_SDP_OFFER, "offer"
        )
        assert answer is not None
        assert answer.sdp_type == SDPType.ANSWER
        assert answer.sdp is not None

        # Step 4: Send ICE candidates
        result = handle_ice_candidate(
            member1.id,
            voice_channel.id,
            SAMPLE_ICE_CANDIDATE,
            sdp_mid="0",
            sdp_mline_index=0,
        )
        assert result is True

        # Step 5: Check connection quality
        quality = get_connection_quality(member1.id, voice_channel.id)
        assert quality is not None

        # Step 6: Disconnect
        result = disconnect_voice(member1.id)
        assert result is True

    def test_multiple_users_in_channel(self, server_with_voice, signaling_setup):
        """Test multiple users connecting to the same channel."""
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

        # User 1 connects
        info1 = create_voice_connection(member1.id, voice_channel.id)
        assert info1 is not None

        # User 2 connects
        info2 = create_voice_connection(member2.id, voice_channel.id)
        assert info2 is not None

        # Both should have different session IDs
        assert info1.session_id != info2.session_id

        # Both can send SDP offers
        answer1 = handle_sdp_offer(member1.id, voice_channel.id, SAMPLE_SDP_OFFER)
        answer2 = handle_sdp_offer(member2.id, voice_channel.id, SAMPLE_SDP_OFFER)

        assert answer1 is not None
        assert answer2 is not None

        # Cleanup
        disconnect_voice(member1.id)
        disconnect_voice(member2.id)

    def test_reconnection_after_disconnect(self, server_with_voice, signaling_setup):
        """Test reconnecting after disconnection."""
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

        # Connect
        info1 = create_voice_connection(owner.id, voice_channel.id)
        session1 = info1.session_id

        # Disconnect
        disconnect_voice(owner.id)

        # Reconnect
        info2 = create_voice_connection(owner.id, voice_channel.id)
        session2 = info2.session_id

        # Should have new session
        assert session1 != session2

        # Cleanup
        disconnect_voice(owner.id)

    def test_sdp_offer_auto_creates_connection(
        self, server_with_voice, signaling_setup
    ):
        """Test that SDP offer auto-creates connection if not exists."""
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

        # Send SDP offer without creating connection first
        answer = handle_sdp_offer(member1.id, voice_channel.id, SAMPLE_SDP_OFFER)

        assert answer is not None
        assert answer.sdp_type == SDPType.ANSWER

        # Should now be connected
        quality = get_connection_quality(member1.id, voice_channel.id)
        assert quality is not None

        # Cleanup
        disconnect_voice(member1.id)

    def test_ice_candidate_requires_connection(
        self, server_with_voice, signaling_setup
    ):
        """Test that ICE candidate requires existing connection."""
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
            handle_ice_candidate(999999, voice_channel.id, SAMPLE_ICE_CANDIDATE)

    def test_multiple_ice_candidates(self, server_with_voice, signaling_setup):
        """Test sending multiple ICE candidates."""
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

        candidates = [
            "candidate:1 1 udp 2130706431 192.168.1.100 54321 typ host",
            "candidate:2 1 udp 1694498815 203.0.113.50 12345 typ srflx raddr 192.168.1.100 rport 54321",
            "candidate:3 1 udp 100 198.51.100.10 3478 typ relay raddr 203.0.113.50 rport 12345",
        ]

        for candidate in candidates:
            result = handle_ice_candidate(member1.id, voice_channel.id, candidate)
            assert result is True

        # Cleanup
        disconnect_voice(member1.id)


class TestVoiceConnectionErrors:
    """Tests for voice connection error handling."""

    def test_duplicate_connection_error(self, server_with_voice, signaling_setup):
        """Test error when creating duplicate connection."""
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

        with pytest.raises(AlreadyConnectedError):
            create_voice_connection(member2.id, voice_channel.id)

        # Cleanup
        disconnect_voice(member2.id)

    def test_quality_check_not_connected(self, server_with_voice, signaling_setup):
        """Test error when checking quality without connection."""
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
            get_connection_quality(999999, voice_channel.id)

    def test_disconnect_wrong_channel(self, server_with_voice, signaling_setup):
        """Test disconnecting from wrong channel returns False."""
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

        # Try to disconnect from different channel
        result = disconnect_voice(member1.id, channel_id=999999)
        assert result is False

        # Should still be connected
        quality = get_connection_quality(member1.id, voice_channel.id)
        assert quality is not None

        # Cleanup
        disconnect_voice(member1.id)
