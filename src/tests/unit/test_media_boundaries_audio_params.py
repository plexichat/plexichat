"""Audio processing parameters are constrained to valid ranges."""

import pytest

from src.core.voice.boundaries import (
    MAX_CRF,
    MAX_FRAME_DURATION_MS,
    MAX_SAMPLE_RATE,
    VALID_SAMPLE_RATES,
    validate_audio_profile,
    validate_crf,
    validate_frame_duration_ms,
    validate_sample_rate,
)
from src.core.voice.signaling.sfu.acoustic_defense import (
    AcousticDefenseConfig,
    _generate_noise_frame,
)


class TestCrf:
    @pytest.mark.parametrize("value", [0, 18, 23, 51, MAX_CRF, "20"])
    def test_valid_crf(self, value):
        assert validate_crf(value) == int(value)

    @pytest.mark.parametrize("value", [-1, MAX_CRF + 1, "abc", None, 1.5])
    def test_invalid_crf_rejected(self, value):
        with pytest.raises(ValueError):
            validate_crf(value)


class TestSampleRate:
    @pytest.mark.parametrize("rate", sorted(VALID_SAMPLE_RATES))
    def test_valid_rates(self, rate):
        assert validate_sample_rate(rate) == rate

    @pytest.mark.parametrize("rate", [0, 7999, MAX_SAMPLE_RATE + 1, 12345, "abc"])
    def test_invalid_rates_rejected(self, rate):
        with pytest.raises(ValueError):
            validate_sample_rate(rate)


class TestFrameDuration:
    @pytest.mark.parametrize("value", [5, 10, 20, MAX_FRAME_DURATION_MS])
    def test_valid_duration(self, value):
        assert validate_frame_duration_ms(value) == value

    @pytest.mark.parametrize("value", [0, 4, MAX_FRAME_DURATION_MS + 1, "x"])
    def test_invalid_duration_rejected(self, value):
        with pytest.raises(ValueError):
            validate_frame_duration_ms(value)

    def test_profile_validates_all_fields(self):
        result = validate_audio_profile(
            {"sample_rate": 48000, "frame_duration_ms": 20, "crf": 23}
        )
        assert result == {"sample_rate": 48000, "frame_duration_ms": 20, "crf": 23}
        with pytest.raises(ValueError):
            validate_audio_profile("nope")


class TestAcousticDefenseConfig:
    def test_defaults_valid(self):
        cfg = AcousticDefenseConfig()
        assert cfg.sample_rate == 48000
        assert cfg.frame_duration_ms == 20

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"sample_rate": 12345},
            {"frame_duration_ms": 1000},
            {"encoding_crf": 99},
        ],
    )
    def test_invalid_audio_params_rejected(self, kwargs):
        with pytest.raises(ValueError):
            AcousticDefenseConfig(**kwargs)

    def test_noise_generator_validates_rate(self):
        with pytest.raises(ValueError):
            _generate_noise_frame(12345, 100, 1000.0, 8000.0, 0.1)
