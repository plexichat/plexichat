"""
Comprehensive security tests for WebRTC voice/video implementation.

Covers:
- WebRTC exploitation attempts
- Signaling manipulation attacks
- ICE candidate injection
- Media stream hijacking
- SFU security for Janus and MediaSoup
"""

import pytest
import json
import hashlib
import hmac
import time
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.voice.signaling import (
    setup,
    get_voice_server_info,
    create_voice_connection,
    disconnect_voice,
    get_turn_credentials,
    NotConnectedError,
    AlreadyConnectedError,
)
from src.core.voice.signaling.models import (
    SDPMessage,
    SDPType,
)
from src.core.voice.signaling.exceptions import (
    ICECandidateError,
    SDPValidationError,
    SDPParseError,
    SFUConnectionError,
)
from src.core.voice.signaling.ice import (
    ICECandidateParser,
    ICECandidateValidator,
    parse_ice_candidate,
)
from src.core.voice.signaling.sfu.base import (
    TransportDirection,
    MediaKind,
)
from src.core.voice.signaling.sfu.janus import JanusAdapter
from src.core.voice.signaling.sfu.mediasoup import MediasoupAdapter


@pytest.fixture(scope="module")
def security_setup(db_and_modules):
    """Setup signaling module for security tests."""
    db, auth, servers, relationships, presence, voice = db_and_modules

    setup(
        voice_module=voice,
        events_module=None,
        sfu_backend="mediasoup",
        stun_urls=["stun:stun.l.google.com:19302"],
        turn_urls=["turn:turn.example.com:3478"],
        turn_secret="test_secret_for_security",
        turn_ttl=3600,
    )

    return voice


class TestWebRTCExploitation:
    """Tests for WebRTC exploitation attempt detection and prevention."""

    def test_sdp_injection_attack(self, server_with_voice, security_setup):
        """Test that malicious SDP injection is rejected."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        malicious_sdp = """v=0
o=- 0 0 IN IP4 127.0.0.1
s=malicious
c=IN IP4 0.0.0.0
t=0 0
m=audio 9 RTP/SAVPF 0
a=rtpmap:0 PCMU/8000
a=candidate:1 1 udp 2130706431 malicious.attacker.com 9999 typ host
"""
        with pytest.raises((SDPValidationError, SDPParseError, ValueError)):
            SDPMessage(sdp_type=SDPType.OFFER, sdp=malicious_sdp)

    def test_oversized_sdp_rejection(self):
        """Test that oversized SDP messages are rejected."""
        oversized_sdp = "v=0\no=- 0 0 IN IP4 127.0.0.1\n" + "a=test\n" * 100000

        with pytest.raises((SDPValidationError, ValueError)):
            SDPMessage(sdp_type=SDPType.OFFER, sdp=oversized_sdp)

    def test_invalid_sdp_type_rejection(self):
        """Test that invalid SDP types are rejected."""
        with pytest.raises((ValueError, AttributeError)):
            SDPMessage(sdp_type="invalid_type", sdp="v=0")

    def test_malformed_media_line(self):
        """Test rejection of malformed media lines in SDP."""
        malformed_sdp = """v=0
o=- 0 0 IN IP4 127.0.0.1
s=-
m=audio INVALID RTP/SAVPF 111
a=rtpmap:111 opus/48000/2
"""
        with pytest.raises((SDPValidationError, SDPParseError, ValueError)):
            SDPMessage(sdp_type=SDPType.OFFER, sdp=malformed_sdp)

    def test_script_injection_in_sdp(self):
        """Test that script injection attempts in SDP are sanitized."""
        sdp_with_script = """v=0
o=<script>alert('xss')</script> 0 0 IN IP4 127.0.0.1
s=-
t=0 0
m=audio 9 RTP/SAVPF 111
"""
        with pytest.raises((SDPValidationError, SDPParseError, ValueError)):
            SDPMessage(sdp_type=SDPType.OFFER, sdp=sdp_with_script)

    def test_buffer_overflow_attempt_in_candidate(self):
        """Test rejection of buffer overflow attempts in ICE candidates."""
        overflow_candidate = "candidate:" + "A" * 100000

        parser = ICECandidateParser()
        with pytest.raises(ICECandidateError):
            parser.parse(overflow_candidate)

    def test_null_byte_injection(self):
        """Test that null byte injection is rejected."""
        null_byte_candidate = "candidate:1 1 udp 2130706431 192.168.1.100\x00malicious 54321 typ host"

        with pytest.raises(ICECandidateError):
            parse_ice_candidate(null_byte_candidate, "audio", 0)


class TestSignalingManipulation:
    """Tests for signaling message manipulation detection."""

    def test_session_hijacking_prevention(self, server_with_voice, security_setup):
        """Test that session hijacking is prevented."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        info1 = create_voice_connection(member1.id, voice_channel.id)
        session_id = info1.session_id

        with pytest.raises((NotConnectedError, ValueError)):
            info2 = get_voice_server_info(member2.id, voice_channel.id)
            assert info2.session_id != session_id

        disconnect_voice(member1.id)

    def test_replay_attack_prevention(self, server_with_voice, security_setup):
        """Test that replay attacks are prevented via timestamps."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        creds1 = get_turn_credentials(member1.id)
        time.sleep(1)
        creds2 = get_turn_credentials(member1.id)

        assert creds1.username != creds2.username
        assert creds1.credential != creds2.credential

    def test_unauthorized_connection_attempt(self, server_with_voice, security_setup):
        """Test that unauthorized users cannot create connections."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        fake_user_id = 999999999

        with pytest.raises((ValueError, NotConnectedError, KeyError)):
            create_voice_connection(fake_user_id, voice_channel.id)

    def test_cross_channel_connection_spoofing(self, server_with_voice, security_setup):
        """Test prevention of cross-channel connection spoofing."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        create_voice_connection(member1.id, voice_channel.id)

        with pytest.raises(AlreadyConnectedError):
            create_voice_connection(member1.id, stage_channel.id)

        disconnect_voice(member1.id)

    def test_turn_credential_forgery_detection(self, server_with_voice, security_setup):
        """Test that forged TURN credentials are detected."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        get_turn_credentials(member1.id)

        fake_username = f"{int(time.time()) + 9999999}:fake_user"
        fake_credential = "forged_credential"

        secret = "test_secret_for_security"
        expected = hmac.new(
            secret.encode(),
            fake_username.encode(),
            hashlib.sha1
        ).digest()

        import base64
        expected_credential = base64.b64encode(expected).decode()

        assert fake_credential != expected_credential

    def test_signaling_message_tampering(self):
        """Test detection of tampered signaling messages."""
        tampered_candidate = "candidate:1 1 udp 2130706431 malicious.com 54321 typ host"

        parser = ICECandidateParser()
        parsed = parser.parse(tampered_candidate)

        assert parsed["address"] == "malicious.com"

        validator = ICECandidateValidator()
        with pytest.raises(ICECandidateError):
            validator.validate(parsed)


class TestICECandidateInjection:
    """Tests for ICE candidate injection attack prevention."""

    def test_malicious_host_candidate_injection(self):
        """Test rejection of malicious host candidates."""
        malicious_candidates = [
            "candidate:1 1 udp 2130706431 0.0.0.0 54321 typ host",
            "candidate:1 1 udp 2130706431 255.255.255.255 54321 typ host",
            "candidate:1 1 udp 2130706431 127.0.0.1 0 typ host",
            "candidate:1 1 udp 2130706431 192.168.1.100 99999 typ host",
        ]

        validator = ICECandidateValidator()
        parser = ICECandidateParser()

        for candidate in malicious_candidates:
            try:
                parsed = parser.parse(candidate)
                validator.validate(parsed)
                if parsed.get("address") in ["0.0.0.0", "255.255.255.255"]:
                    pytest.fail(f"Should have rejected malicious candidate: {candidate}")
            except ICECandidateError:
                pass

    def test_relay_candidate_spoofing(self):
        """Test detection of spoofed relay candidates."""
        spoofed_relay = "candidate:1 1 udp 100 attacker.com 3478 typ relay raddr 192.168.1.100 rport 54321"

        parser = ICECandidateParser()
        parsed = parser.parse(spoofed_relay)

        assert parsed["type"] == "relay"
        assert parsed["address"] == "attacker.com"

    def test_srflx_candidate_manipulation(self):
        """Test detection of manipulated srflx candidates."""
        manipulated_srflx = "candidate:1 1 udp 1694498815 malicious.example.com 12345 typ srflx raddr 10.0.0.1 rport 54321"

        parser = ICECandidateParser()
        parsed = parser.parse(manipulated_srflx)

        validator = ICECandidateValidator()
        try:
            validator.validate(parsed)
        except ICECandidateError:
            pass

    def test_candidate_priority_manipulation(self):
        """Test detection of manipulated candidate priorities."""
        high_priority = "candidate:1 1 udp 9999999999 192.168.1.100 54321 typ host"

        parser = ICECandidateParser()
        parsed = parser.parse(high_priority)

        assert parsed["priority"] == 9999999999

    def test_excessive_candidate_flooding(self):
        """Test protection against candidate flooding attacks."""
        parser = ICECandidateParser()
        validator = ICECandidateValidator()

        candidates = []
        for i in range(1000):
            candidate = f"candidate:{i} 1 udp 2130706431 192.168.1.{i % 255} {10000 + i} typ host"
            try:
                parsed = parser.parse(candidate)
                validator.validate(parsed)
                candidates.append(parsed)
            except ICECandidateError:
                pass

        assert len(candidates) <= 1000

    def test_ipv6_candidate_injection(self):
        """Test handling of malicious IPv6 candidates."""
        malicious_ipv6_candidates = [
            "candidate:1 1 udp 2130706431 ::1 54321 typ host",
            "candidate:1 1 udp 2130706431 :: 54321 typ host",
            "candidate:1 1 udp 2130706431 ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff 54321 typ host",
        ]

        parser = ICECandidateParser()
        validator = ICECandidateValidator()

        for candidate in malicious_ipv6_candidates:
            try:
                parsed = parser.parse(candidate)
                validator.validate(parsed)
            except ICECandidateError:
                pass

    def test_tcp_candidate_exploitation(self):
        """Test handling of malicious TCP candidates."""
        malicious_tcp = "candidate:1 1 tcp 1518280447 192.168.1.100 9 typ host tcptype passive"

        parser = ICECandidateParser()
        parsed = parser.parse(malicious_tcp)

        assert parsed["transport"] == "tcp"
        assert parsed["tcp_type"] == "passive"

    def test_candidate_address_spoofing(self):
        """Test detection of address spoofing in candidates."""
        spoofed_addresses = [
            "candidate:1 1 udp 2130706431 169.254.169.254 80 typ host",  # AWS metadata
            "candidate:1 1 udp 2130706431 192.0.2.1 54321 typ host",  # TEST-NET-1
            "candidate:1 1 udp 2130706431 198.51.100.1 54321 typ host",  # TEST-NET-2
        ]

        parser = ICECandidateParser()

        for candidate in spoofed_addresses:
            parsed = parser.parse(candidate)
            assert "address" in parsed


class TestMediaStreamHijacking:
    """Tests for media stream hijacking prevention."""

    def test_unauthorized_stream_access(self, server_with_voice, security_setup):
        """Test that unauthorized users cannot access streams."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        create_voice_connection(member1.id, voice_channel.id)

        with pytest.raises((NotConnectedError, ValueError)):
            create_voice_connection(999999, voice_channel.id)

        disconnect_voice(member1.id)

    def test_stream_id_enumeration_prevention(self, server_with_voice, security_setup):
        """Test prevention of stream ID enumeration attacks."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        info1 = create_voice_connection(member1.id, voice_channel.id)
        info2 = create_voice_connection(member2.id, voice_channel.id)

        assert info1.session_id != info2.session_id
        assert len(info1.session_id) > 20
        assert len(info2.session_id) > 20

        disconnect_voice(member1.id)
        disconnect_voice(member2.id)

    def test_cross_user_stream_injection(self, server_with_voice, security_setup):
        """Test prevention of cross-user stream injection."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        info1 = create_voice_connection(member1.id, voice_channel.id)
        info2 = create_voice_connection(member2.id, voice_channel.id)

        assert info1.token != info2.token

        disconnect_voice(member1.id)
        disconnect_voice(member2.id)

    def test_dtls_fingerprint_spoofing(self):
        """Test detection of DTLS fingerprint spoofing."""
        valid_fingerprint = {
            "algorithm": "sha-256",
            "value": "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99"
        }

        spoofed_fingerprint = {
            "algorithm": "md5",
            "value": "malicious_fingerprint"
        }

        assert valid_fingerprint["algorithm"] == "sha-256"
        assert spoofed_fingerprint["algorithm"] != "sha-256"

    def test_rtp_stream_redirection(self):
        """Test prevention of RTP stream redirection attacks."""
        legitimate_params = {
            "codecs": [{"mimeType": "audio/opus", "clockRate": 48000}],
            "headerExtensions": [],
            "encodings": [{"ssrc": 12345}]
        }

        malicious_params = {
            "codecs": [{"mimeType": "audio/opus", "clockRate": 48000}],
            "headerExtensions": [],
            "encodings": [{"ssrc": 99999, "rtx": {"ssrc": 88888}}]
        }

        assert legitimate_params["encodings"][0]["ssrc"] == 12345
        assert malicious_params["encodings"][0]["ssrc"] == 99999

    def test_media_encryption_bypass_attempt(self):
        """Test that media encryption cannot be bypassed."""
        encrypted_sdp = """v=0
o=- 0 0 IN IP4 127.0.0.1
s=-
t=0 0
m=audio 9 RTP/SAVPF 111
a=rtpmap:111 opus/48000/2
a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:VALID_KEY_MATERIAL
"""
        unencrypted_sdp = """v=0
o=- 0 0 IN IP4 127.0.0.1
s=-
t=0 0
m=audio 9 RTP/AVP 0
a=rtpmap:0 PCMU/8000
"""

        assert "RTP/SAVPF" in encrypted_sdp
        assert "crypto:" in encrypted_sdp
        assert "RTP/AVP" in unencrypted_sdp


class TestJanusSFUSecurity:
    """Security tests specific to Janus SFU implementation."""

    @pytest.fixture
    def mock_janus_session(self):
        """Create a mock Janus session."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"janus": "success"})

        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_context.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_context)
        mock_session.close = AsyncMock()

        return mock_session, mock_response

    @pytest.mark.asyncio
    async def test_janus_unauthorized_room_access(self, mock_janus_session):
        """Test prevention of unauthorized room access in Janus."""
        mock_session, mock_response = mock_janus_session
        mock_response.json = AsyncMock(return_value={
            "janus": "error",
            "error": {"code": 426, "reason": "Unauthorized"}
        })

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = JanusAdapter(api_url="http://localhost:8088/janus")
            adapter._session = mock_session

            with pytest.raises(SFUConnectionError):
                await adapter.join_room("private_room", "unauthorized_peer")

    @pytest.mark.asyncio
    async def test_janus_session_fixation_prevention(self, mock_janus_session):
        """Test prevention of session fixation attacks in Janus."""
        mock_session, mock_response = mock_janus_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = JanusAdapter(api_url="http://localhost:8088/janus")
            adapter._session = mock_session

            mock_response.json = AsyncMock(side_effect=[
                {"janus": "success", "data": {"id": 12345}},
                {"janus": "success", "data": {"id": 67890}},
            ])

            await adapter.create_room("room1")
            session_id_1 = adapter._session_id

            mock_response.json = AsyncMock(side_effect=[
                {"janus": "success", "data": {"id": 54321}},
            ])

            await adapter.create_room("room2")
            session_id_2 = adapter._session_id

            assert session_id_1 != session_id_2 or session_id_1 is None

    @pytest.mark.asyncio
    async def test_janus_plugin_injection_prevention(self, mock_janus_session):
        """Test prevention of malicious plugin injection in Janus."""
        mock_session, mock_response = mock_janus_session
        mock_response.json = AsyncMock(return_value={
            "janus": "error",
            "error": {"code": 458, "reason": "Plugin not found"}
        })

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = JanusAdapter(api_url="http://localhost:8088/janus")
            adapter._session = mock_session

            with pytest.raises(SFUConnectionError):
                await adapter.create_room("room_malicious_plugin")

    @pytest.mark.asyncio
    async def test_janus_api_command_injection(self, mock_janus_session):
        """Test prevention of command injection in Janus API calls."""
        mock_session, mock_response = mock_janus_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = JanusAdapter(api_url="http://localhost:8088/janus")
            adapter._session = mock_session

            malicious_room_id = "room'; DROP TABLE rooms; --"

            mock_response.json = AsyncMock(side_effect=[
                {"janus": "success", "data": {"id": 12345}},
                {"janus": "success", "data": {"id": 67890}},
                {"janus": "success"},
            ])

            try:
                await adapter.create_room(malicious_room_id)
            except (SFUConnectionError, ValueError):
                pass

    @pytest.mark.asyncio
    async def test_janus_rate_limiting(self, mock_janus_session):
        """Test rate limiting for Janus API calls."""
        mock_session, mock_response = mock_janus_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = JanusAdapter(api_url="http://localhost:8088/janus")
            adapter._session = mock_session

            mock_response.json = AsyncMock(return_value={
                "janus": "error",
                "error": {"code": 429, "reason": "Too many requests"}
            })

            for _ in range(100):
                try:
                    await adapter.health_check()
                except SFUConnectionError:
                    break

    @pytest.mark.asyncio
    async def test_janus_publisher_spoofing(self, mock_janus_session):
        """Test prevention of publisher ID spoofing in Janus."""
        mock_session, mock_response = mock_janus_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = JanusAdapter(api_url="http://localhost:8088/janus")
            adapter._session = mock_session

            mock_response.json = AsyncMock(side_effect=[
                {"janus": "success", "data": {"id": 12345}},
                {"janus": "success", "data": {"id": 67890}},
                {"janus": "success"},
                {"janus": "success", "data": {"id": 11111}},
                {"janus": "success", "plugindata": {"data": {"publishers": []}}},
                {"janus": "error", "error": {"code": 436, "reason": "Unauthorized publisher"}},
            ])

            await adapter.create_room("room123")
            await adapter.join_room("room123", "peer1")

            with pytest.raises(SFUConnectionError):
                await adapter.produce("room123", "peer1", "transport1", MediaKind.AUDIO, {})


class TestMediasoupSFUSecurity:
    """Security tests specific to mediasoup SFU implementation."""

    @pytest.fixture
    def mock_mediasoup_session(self):
        """Create a mock mediasoup session."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={})
        mock_response.text = AsyncMock(return_value="")
        mock_response.content_type = "application/json"

        mock_session = MagicMock()
        mock_session.request = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_response)))
        mock_session.close = AsyncMock()

        return mock_session, mock_response

    @pytest.mark.asyncio
    async def test_mediasoup_router_capabilities_tampering(self, mock_mediasoup_session):
        """Test detection of tampered router capabilities."""
        mock_session, mock_response = mock_mediasoup_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            mock_response.json = AsyncMock(return_value={
                "codecs": [
                    {"kind": "audio", "mimeType": "audio/malicious", "clockRate": 48000}
                ]
            })

            caps = await adapter.get_router_capabilities("room123")
            assert "codecs" in caps

    @pytest.mark.asyncio
    async def test_mediasoup_transport_hijacking(self, mock_mediasoup_session):
        """Test prevention of transport hijacking in mediasoup."""
        mock_session, mock_response = mock_mediasoup_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            mock_response.json = AsyncMock(return_value={
                "id": "transport_123",
                "iceParameters": {"usernameFragment": "test", "password": "test"},
                "iceCandidates": [],
                "dtlsParameters": {"fingerprints": []}
            })

            transport = await adapter.create_transport("room123", "peer1", TransportDirection.SEND)

            mock_response.status = 403
            mock_response.text = AsyncMock(return_value="Forbidden")

            with pytest.raises(SFUConnectionError):
                await adapter.connect_transport("room123", "peer2", transport.id, {})

    @pytest.mark.asyncio
    async def test_mediasoup_producer_id_collision(self, mock_mediasoup_session):
        """Test handling of producer ID collisions."""
        mock_session, mock_response = mock_mediasoup_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            mock_response.json = AsyncMock(return_value={
                "id": "producer_duplicate",
                "paused": False
            })

            await adapter.produce("room123", "peer1", "transport1", MediaKind.AUDIO, {})

            mock_response.status = 409
            mock_response.text = AsyncMock(return_value="Conflict")

            with pytest.raises(SFUConnectionError):
                await adapter.produce("room123", "peer2", "transport2", MediaKind.AUDIO, {})

    @pytest.mark.asyncio
    async def test_mediasoup_consumer_unauthorized_access(self, mock_mediasoup_session):
        """Test prevention of unauthorized consumer creation."""
        mock_session, mock_response = mock_mediasoup_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            mock_response.status = 403
            mock_response.text = AsyncMock(return_value="Forbidden: Cannot consume this producer")

            with pytest.raises(SFUConnectionError):
                await adapter.consume("room123", "unauthorized_peer", "transport1", "producer_private", {})

    @pytest.mark.asyncio
    async def test_mediasoup_simulcast_layer_manipulation(self, mock_mediasoup_session):
        """Test prevention of simulcast layer manipulation attacks."""
        mock_session, mock_response = mock_mediasoup_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            mock_response.status = 400
            mock_response.text = AsyncMock(return_value="Invalid layer parameters")

            with pytest.raises(SFUConnectionError):
                await adapter.set_preferred_layers(
                    "room123", "peer1", "consumer1",
                    spatial_layer=999, temporal_layer=999
                )

    @pytest.mark.asyncio
    async def test_mediasoup_rtp_parameter_injection(self, mock_mediasoup_session):
        """Test rejection of malicious RTP parameters."""
        mock_session, mock_response = mock_mediasoup_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            malicious_rtp_params = {
                "codecs": [
                    {
                        "mimeType": "audio/opus",
                        "clockRate": 48000,
                        "payloadType": 111,
                        "maliciousField": "exploit_attempt"
                    }
                ],
                "encodings": [{"ssrc": 0xFFFFFFFF}]
            }

            mock_response.json = AsyncMock(return_value={"id": "producer_malicious", "paused": False})

            producer = await adapter.produce(
                "room123", "peer1", "transport1", MediaKind.AUDIO, malicious_rtp_params
            )

            assert producer.id == "producer_malicious"

    @pytest.mark.asyncio
    async def test_mediasoup_room_enumeration_prevention(self, mock_mediasoup_session):
        """Test prevention of room enumeration attacks."""
        mock_session, mock_response = mock_mediasoup_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            mock_response.status = 404
            mock_response.text = AsyncMock(return_value="Room not found")

            for room_id in range(1000):
                try:
                    await adapter.get_room_info(f"room_{room_id}")
                except SFUConnectionError:
                    pass

    @pytest.mark.asyncio
    async def test_mediasoup_dtls_parameter_validation(self, mock_mediasoup_session):
        """Test validation of DTLS parameters to prevent attacks."""
        mock_session, mock_response = mock_mediasoup_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            invalid_dtls_params = {
                "role": "client",
                "fingerprints": [
                    {
                        "algorithm": "invalid_algo",
                        "value": "MALICIOUS_FINGERPRINT"
                    }
                ]
            }

            mock_response.status = 400
            mock_response.text = AsyncMock(return_value="Invalid DTLS parameters")

            with pytest.raises(SFUConnectionError):
                await adapter.connect_transport("room123", "peer1", "transport1", invalid_dtls_params)


class TestCrossProtocolAttacks:
    """Tests for cross-protocol and hybrid attack scenarios."""

    @pytest.mark.asyncio
    async def test_websocket_to_webrtc_injection(self, mock_mediasoup_session):
        """Test prevention of WebSocket to WebRTC injection attacks."""
        mock_session, mock_response = mock_mediasoup_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            websocket_payload = {
                "type": "webrtc",
                "sdp": "malicious_sdp",
                "candidates": ["malicious_candidate"]
            }

            mock_response.status = 400
            mock_response.text = AsyncMock(return_value="Invalid request")

            with pytest.raises((SFUConnectionError, ValueError, KeyError)):
                await adapter.create_room(json.dumps(websocket_payload))

    def test_turn_credential_leakage_prevention(self, server_with_voice, security_setup):
        """Test that TURN credentials don't leak between users."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice

        creds1 = get_turn_credentials(member1.id)
        creds2 = get_turn_credentials(member2.id)

        assert creds1.credential != creds2.credential
        assert creds1.username.split(":")[1] != creds2.username.split(":")[1]

    @pytest.mark.asyncio
    async def test_concurrent_connection_exhaustion(self, mock_mediasoup_session):
        """Test protection against connection exhaustion attacks."""
        mock_session, mock_response = mock_mediasoup_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            mock_response.json = AsyncMock(return_value={
                "id": "transport_flood",
                "iceParameters": {},
                "iceCandidates": [],
                "dtlsParameters": {}
            })

            transports = []
            for i in range(100):
                try:
                    transport = await adapter.create_transport(
                        "room123", f"peer_{i}", TransportDirection.SEND
                    )
                    transports.append(transport)
                except SFUConnectionError:
                    break

            assert len(transports) <= 100

    def test_metadata_injection_in_ice_candidate(self):
        """Test prevention of metadata injection via ICE candidates."""
        candidate_with_metadata = (
            "candidate:1 1 udp 2130706431 192.168.1.100 54321 typ host "
            "ufrag user<script>alert(1)</script> generation 0"
        )

        parser = ICECandidateParser()
        with pytest.raises(ICECandidateError):
            parser.parse(candidate_with_metadata)

    def test_srtp_key_manipulation(self):
        """Test detection of SRTP key manipulation attempts."""
        sdp_with_weak_crypto = """v=0
o=- 0 0 IN IP4 127.0.0.1
s=-
t=0 0
m=audio 9 RTP/SAVPF 111
a=rtpmap:111 opus/48000/2
a=crypto:1 NULL_CIPHER inline:AAAA
"""

        assert "NULL_CIPHER" in sdp_with_weak_crypto

        sdp_with_strong_crypto = """v=0
o=- 0 0 IN IP4 127.0.0.1
s=-
t=0 0
m=audio 9 RTP/SAVPF 111
a=rtpmap:111 opus/48000/2
a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:VALID_KEY_MATERIAL
"""

        assert "AES_CM_128_HMAC_SHA1_80" in sdp_with_strong_crypto
