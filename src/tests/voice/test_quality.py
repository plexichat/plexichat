"""
Tests for connection quality monitoring.
"""

import pytest

from src.core.voice.signaling import (
    setup,
    create_voice_connection,
    get_connection_quality,
    update_quality_hint,
    disconnect_voice,
    NotConnectedError,
)
from src.core.voice.signaling.models import (
    ConnectionQuality,
    QualityLevel,
    QUALITY_BITRATE_THRESHOLDS,
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


class TestConnectionQuality:
    """Tests for connection quality monitoring."""
    
    def test_get_connection_quality(self, server_with_voice, signaling_setup):
        """Test getting connection quality."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice
        
        create_voice_connection(member1.id, voice_channel.id)
        
        quality = get_connection_quality(member1.id, voice_channel.id)
        
        assert quality is not None
        assert isinstance(quality, ConnectionQuality)
        assert quality.user_id == member1.id
        assert quality.channel_id == voice_channel.id
        assert quality.quality_level is not None
        assert quality.bitrate > 0
        assert quality.packet_loss >= 0
        assert quality.jitter >= 0
        assert quality.round_trip_time >= 0
        assert quality.timestamp > 0
        
        # Cleanup
        disconnect_voice(member1.id)
    
    def test_get_quality_not_connected(self, server_with_voice, signaling_setup):
        """Test getting quality when not connected."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice
        
        with pytest.raises(NotConnectedError):
            get_connection_quality(999999, voice_channel.id)
    
    def test_update_quality_hint_bitrate(self, server_with_voice, signaling_setup):
        """Test updating quality hint with target bitrate."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice
        
        create_voice_connection(member1.id, voice_channel.id)
        
        result = update_quality_hint(member1.id, voice_channel.id, target_bitrate=128000)
        
        assert result is True
        
        quality = get_connection_quality(member1.id, voice_channel.id)
        assert quality.bitrate == 128000
        
        # Cleanup
        disconnect_voice(member1.id)
    
    def test_update_quality_hint_level(self, server_with_voice, signaling_setup):
        """Test updating quality hint with quality level."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice
        
        create_voice_connection(member1.id, voice_channel.id)
        
        result = update_quality_hint(member1.id, voice_channel.id, quality_level="excellent")
        
        assert result is True
        
        quality = get_connection_quality(member1.id, voice_channel.id)
        assert quality.quality_level == QualityLevel.EXCELLENT
        
        # Cleanup
        disconnect_voice(member1.id)
    
    def test_update_quality_hint_not_connected(self, server_with_voice, signaling_setup):
        """Test updating quality hint when not connected."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice
        
        result = update_quality_hint(999999, voice_channel.id, target_bitrate=64000)
        
        assert result is False
    
    def test_quality_to_dict(self, server_with_voice, signaling_setup):
        """Test ConnectionQuality serialization."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice
        
        create_voice_connection(member1.id, voice_channel.id)
        
        quality = get_connection_quality(member1.id, voice_channel.id)
        data = quality.to_dict()
        
        assert "user_id" in data
        assert "channel_id" in data
        assert "quality_level" in data
        assert "bitrate" in data
        assert "packet_loss" in data
        assert "jitter" in data
        assert "round_trip_time" in data
        assert "timestamp" in data
        
        # Cleanup
        disconnect_voice(member1.id)


class TestQualityLevels:
    """Tests for quality level handling."""
    
    def test_quality_levels_enum(self):
        """Test QualityLevel enum values."""
        assert QualityLevel.EXCELLENT.value == "excellent"
        assert QualityLevel.GOOD.value == "good"
        assert QualityLevel.FAIR.value == "fair"
        assert QualityLevel.POOR.value == "poor"
        assert QualityLevel.CRITICAL.value == "critical"
    
    def test_quality_bitrate_thresholds(self):
        """Test quality bitrate thresholds are defined."""
        assert QualityLevel.EXCELLENT in QUALITY_BITRATE_THRESHOLDS
        assert QualityLevel.GOOD in QUALITY_BITRATE_THRESHOLDS
        assert QualityLevel.FAIR in QUALITY_BITRATE_THRESHOLDS
        assert QualityLevel.POOR in QUALITY_BITRATE_THRESHOLDS
        assert QualityLevel.CRITICAL in QUALITY_BITRATE_THRESHOLDS
        
        for level, thresholds in QUALITY_BITRATE_THRESHOLDS.items():
            assert "min" in thresholds
            assert "max" in thresholds
            assert thresholds["min"] < thresholds["max"]
    
    def test_excellent_has_highest_bitrate(self):
        """Test that excellent quality has highest bitrate."""
        excellent = QUALITY_BITRATE_THRESHOLDS[QualityLevel.EXCELLENT]
        critical = QUALITY_BITRATE_THRESHOLDS[QualityLevel.CRITICAL]
        
        assert excellent["max"] > critical["max"]
        assert excellent["min"] > critical["min"]
    
    def test_update_quality_with_invalid_level(self, server_with_voice, signaling_setup):
        """Test updating quality with invalid level uses default."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice
        
        create_voice_connection(member1.id, voice_channel.id)
        
        # Invalid level should not raise, just use default
        result = update_quality_hint(member1.id, voice_channel.id, quality_level="invalid_level")
        
        assert result is True
        
        quality = get_connection_quality(member1.id, voice_channel.id)
        assert quality.quality_level == QualityLevel.GOOD  # Default
        
        # Cleanup
        disconnect_voice(member1.id)


class TestQualityMonitoring:
    """Tests for quality monitoring scenarios."""
    
    def test_quality_updates_timestamp(self, server_with_voice, signaling_setup):
        """Test that quality updates include timestamp."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice
        
        create_voice_connection(member1.id, voice_channel.id)
        
        quality1 = get_connection_quality(member1.id, voice_channel.id)
        
        update_quality_hint(member1.id, voice_channel.id, target_bitrate=96000)
        
        quality2 = get_connection_quality(member1.id, voice_channel.id)
        
        assert quality2.timestamp >= quality1.timestamp
        
        # Cleanup
        disconnect_voice(member1.id)
    
    def test_quality_level_affects_bitrate(self, server_with_voice, signaling_setup):
        """Test that quality level affects suggested bitrate."""
        owner, member1, member2, server, voice_channel, stage_channel, servers, voice = server_with_voice
        
        create_voice_connection(member1.id, voice_channel.id)
        
        # Set to poor quality
        update_quality_hint(member1.id, voice_channel.id, quality_level="poor")
        poor_quality = get_connection_quality(member1.id, voice_channel.id)
        
        # Set to excellent quality
        update_quality_hint(member1.id, voice_channel.id, quality_level="excellent")
        excellent_quality = get_connection_quality(member1.id, voice_channel.id)
        
        assert excellent_quality.bitrate > poor_quality.bitrate
        
        # Cleanup
        disconnect_voice(member1.id)
