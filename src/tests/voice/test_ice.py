"""
Tests for ICE candidate handling.
"""

import pytest

from src.core.voice.signaling.ice import (
    ICECandidateParser,
    ICECandidateValidator,
    ICECandidateManager,
    parse_ice_candidate,
    validate_ice_candidate,
)
from src.core.voice.signaling.models import ICECandidate
from src.core.voice.signaling.exceptions import ICECandidateError


class TestICECandidateParser:
    """Tests for ICE candidate parsing."""

    def test_parse_host_candidate(self):
        """Test parsing a host candidate."""
        candidate = "candidate:1 1 udp 2130706431 192.168.1.100 54321 typ host"

        parser = ICECandidateParser()
        parsed = parser.parse(candidate)

        assert parsed["foundation"] == "1"
        assert parsed["component"] == 1
        assert parsed["transport"] == "udp"
        assert parsed["priority"] == 2130706431
        assert parsed["address"] == "192.168.1.100"
        assert parsed["port"] == 54321
        assert parsed["type"] == "host"

    def test_parse_srflx_candidate(self):
        """Test parsing a server reflexive candidate."""
        candidate = "candidate:2 1 udp 1694498815 203.0.113.50 12345 typ srflx raddr 192.168.1.100 rport 54321"

        parser = ICECandidateParser()
        parsed = parser.parse(candidate)

        assert parsed["type"] == "srflx"
        assert parsed["related_address"] == "192.168.1.100"
        assert parsed["related_port"] == 54321

    def test_parse_relay_candidate(self):
        """Test parsing a relay (TURN) candidate."""
        candidate = "candidate:3 1 udp 100 198.51.100.10 3478 typ relay raddr 203.0.113.50 rport 12345"

        parser = ICECandidateParser()
        parsed = parser.parse(candidate)

        assert parsed["type"] == "relay"
        assert parsed["address"] == "198.51.100.10"
        assert parsed["port"] == 3478

    def test_parse_tcp_candidate(self):
        """Test parsing a TCP candidate."""
        candidate = "candidate:4 1 tcp 1518280447 192.168.1.100 9 typ host tcptype active"

        parser = ICECandidateParser()
        parsed = parser.parse(candidate)

        assert parsed["transport"] == "tcp"
        assert parsed["tcp_type"] == "active"

    def test_parse_candidate_with_prefix(self):
        """Test parsing candidate with a= prefix."""
        candidate = "a=candidate:1 1 udp 2130706431 192.168.1.100 54321 typ host"

        parser = ICECandidateParser()
        parsed = parser.parse(candidate)

        assert parsed["type"] == "host"

    def test_parse_candidate_with_generation(self):
        """Test parsing candidate with generation."""
        candidate = "candidate:1 1 udp 2130706431 192.168.1.100 54321 typ host generation 0"

        parser = ICECandidateParser()
        parsed = parser.parse(candidate)

        assert parsed["generation"] == 0

    def test_parse_candidate_with_ufrag(self):
        """Test parsing candidate with ufrag."""
        candidate = "candidate:1 1 udp 2130706431 192.168.1.100 54321 typ host ufrag abc123"

        parser = ICECandidateParser()
        parsed = parser.parse(candidate)

        assert parsed["ufrag"] == "abc123"

    def test_parse_invalid_candidate_raises(self):
        """Test that invalid candidate raises error."""
        parser = ICECandidateParser()

        with pytest.raises(ICECandidateError):
            parser.parse("invalid candidate string")

    def test_parse_empty_candidate_raises(self):
        """Test that empty candidate raises error."""
        parser = ICECandidateParser()

        with pytest.raises(ICECandidateError):
            parser.parse("")

    def test_to_candidate_object(self):
        """Test converting to ICECandidate object."""
        candidate_str = "candidate:1 1 udp 2130706431 192.168.1.100 54321 typ host"

        parser = ICECandidateParser()
        candidate = parser.to_candidate(candidate_str, sdp_mid="audio", sdp_mline_index=0)

        assert isinstance(candidate, ICECandidate)
        assert candidate.candidate == candidate_str
        assert candidate.sdp_mid == "audio"
        assert candidate.sdp_mline_index == 0


class TestICECandidateValidator:
    """Tests for ICE candidate validation."""

    def test_validate_valid_candidate(self):
        """Test validating a valid candidate."""
        parsed = {
            "foundation": "1",
            "component": 1,
            "transport": "udp",
            "priority": 2130706431,
            "address": "192.168.1.100",
            "port": 54321,
            "type": "host",
        }

        validator = ICECandidateValidator()
        validator.validate(parsed)  # Should not raise

    def test_validate_invalid_transport(self):
        """Test that invalid transport raises error."""
        parsed = {
            "transport": "invalid",
            "type": "host",
            "address": "192.168.1.100",
            "port": 54321,
            "priority": 100,
        }

        validator = ICECandidateValidator()

        with pytest.raises(ICECandidateError):
            validator.validate(parsed)

    def test_validate_invalid_type(self):
        """Test that invalid type raises error."""
        parsed = {
            "transport": "udp",
            "type": "invalid",
            "address": "192.168.1.100",
            "port": 54321,
            "priority": 100,
        }

        validator = ICECandidateValidator()

        with pytest.raises(ICECandidateError):
            validator.validate(parsed)

    def test_validate_invalid_port(self):
        """Test that invalid port raises error."""
        parsed = {
            "transport": "udp",
            "type": "host",
            "address": "192.168.1.100",
            "port": 70000,  # Invalid port
            "priority": 100,
        }

        validator = ICECandidateValidator()

        with pytest.raises(ICECandidateError):
            validator.validate(parsed)

    def test_validate_ipv6_address(self):
        """Test validating IPv6 address."""
        parsed = {
            "transport": "udp",
            "type": "host",
            "address": "2001:db8::1",
            "port": 54321,
            "priority": 100,
        }

        validator = ICECandidateValidator()
        validator.validate(parsed)  # Should not raise


class TestICECandidateManager:
    """Tests for ICE candidate manager."""

    def test_add_candidate(self):
        """Test adding a candidate."""
        manager = ICECandidateManager()
        candidate_str = "candidate:1 1 udp 2130706431 192.168.1.100 54321 typ host"

        candidate = manager.add_candidate("session1", candidate_str, "audio", 0)

        assert candidate is not None
        assert candidate.candidate == candidate_str

    def test_get_candidates(self):
        """Test getting candidates for a session."""
        manager = ICECandidateManager()

        manager.add_candidate("session1", "candidate:1 1 udp 2130706431 192.168.1.100 54321 typ host")
        manager.add_candidate("session1", "candidate:2 1 udp 1694498815 203.0.113.50 12345 typ srflx raddr 192.168.1.100 rport 54321")

        candidates = manager.get_candidates("session1")

        assert len(candidates) == 2

    def test_clear_candidates(self):
        """Test clearing candidates for a session."""
        manager = ICECandidateManager()

        manager.add_candidate("session1", "candidate:1 1 udp 2130706431 192.168.1.100 54321 typ host")
        manager.clear_candidates("session1")

        candidates = manager.get_candidates("session1")
        assert len(candidates) == 0

    def test_get_best_candidate_prefers_relay(self):
        """Test that best candidate prefers relay for NAT traversal."""
        manager = ICECandidateManager()

        manager.add_candidate("session1", "candidate:1 1 udp 2130706431 192.168.1.100 54321 typ host")
        manager.add_candidate("session1", "candidate:2 1 udp 100 198.51.100.10 3478 typ relay raddr 192.168.1.100 rport 54321")

        best = manager.get_best_candidate("session1")

        assert best is not None
        assert "relay" in best.candidate


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_parse_ice_candidate(self):
        """Test parse_ice_candidate function."""
        candidate_str = "candidate:1 1 udp 2130706431 192.168.1.100 54321 typ host"

        candidate = parse_ice_candidate(candidate_str, "audio", 0)

        assert isinstance(candidate, ICECandidate)
        assert candidate.sdp_mid == "audio"

    def test_validate_ice_candidate_valid(self):
        """Test validate_ice_candidate with valid candidate."""
        candidate_str = "candidate:1 1 udp 2130706431 192.168.1.100 54321 typ host"

        result = validate_ice_candidate(candidate_str)

        assert result is True

    def test_validate_ice_candidate_invalid(self):
        """Test validate_ice_candidate with invalid candidate."""
        result = validate_ice_candidate("invalid")

        assert result is False
