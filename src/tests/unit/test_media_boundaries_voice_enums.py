"""Voice region enums, bitrate and numeric metric ranges."""

import pytest

from src.core.voice.boundaries import (
    MAX_BITRATE,
    MAX_REGION_ID_LENGTH,
    MIN_BITRATE,
    VALID_REGION_IDS,
    clamp_bitrate,
    validate_bitrate_input,
    validate_quality_metrics,
    validate_region_id,
)
from src.core.voice.signaling.models import (
    ConnectionQuality,
    QualityLevel,
    VoiceServerInfo,
)


class TestBitrateInput:
    @pytest.mark.parametrize("value", [1000, 64000, 128000, 384000, "128000"])
    def test_valid_input(self, value):
        assert validate_bitrate_input(value) == int(value)

    @pytest.mark.parametrize("value", [True, 1.5, "abc", -1, 2**40, None])
    def test_invalid_input_rejected(self, value):
        with pytest.raises(ValueError):
            validate_bitrate_input(value)

    @pytest.mark.parametrize("value", [0, 1000, 64000, 10**9])
    def test_clamp(self, value):
        assert MIN_BITRATE <= clamp_bitrate(value) <= MAX_BITRATE

    def test_bitrate_clamped_in_server_info(self):
        info = VoiceServerInfo(
            endpoint="wss://voice.example.com/ws",
            token="t",
            ice_servers=[],
            session_id="s1",
            channel_id=1,
            user_id=2,
            bitrate=10**9,
        )
        assert info.bitrate == MAX_BITRATE


class TestRegionEnum:
    def test_none_allowed(self):
        assert validate_region_id(None) is None

    def test_known_regions(self):
        for region in VALID_REGION_IDS:
            assert validate_region_id(region) == region

    @pytest.mark.parametrize(
        "value", ["invalid-region", "us-west\x00", "x" * (MAX_REGION_ID_LENGTH + 1)]
    )
    def test_invalid_regions_rejected(self, value):
        with pytest.raises(ValueError):
            validate_region_id(value)


class TestQualityMetrics:
    def test_bounds_and_filters(self):
        result = validate_quality_metrics(
            {
                "bitrate": 10**9,
                "packet_loss": -5,
                "jitter": float("nan"),
                "round_trip_time": 50,
                "quality_level": "GOOD",
                "bogus": 1,
            }
        )
        assert result["bitrate"] == MAX_BITRATE
        assert result["packet_loss"] == 0.0
        assert "jitter" not in result
        assert result["round_trip_time"] == 50
        assert result["quality_level"] == "good"
        assert "bogus" not in result

    def test_connection_quality_clamps_values(self):
        sample = ConnectionQuality(
            user_id=1,
            channel_id=2,
            quality_level=QualityLevel.GOOD,
            bitrate=10**9,
            packet_loss=-1,
            jitter=float("inf"),
            round_trip_time=-5,
            timestamp=0,
        )
        assert sample.bitrate == MAX_BITRATE
        assert sample.packet_loss == 0.0
        assert sample.jitter == 0.0
        assert sample.round_trip_time == 0.0
