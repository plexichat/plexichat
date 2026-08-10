"""
Tests for the acoustic eavesdropping defense module.

Covers config validation, the band-limited noise generator, transient
shaving, speech presence estimation, and the full audio processing
pipeline (VAD gating, spectral masking, jitter injection) via a fake
source track.
"""

import random
from unittest.mock import patch

import pytest

from src.core.voice.signaling.sfu.acoustic_defense import (
    AcousticDefenseConfig,
    AudioProcessingTrack,
    _apply_transient_shaving,
    _estimate_speech_presence,
    _generate_noise_frame,
)

SAMPLE_RATE = 48000
FRAME_SAMPLES = 960  # 20ms at 48kHz


class _FakeFrame:
    def __init__(self, data):
        self.data = data


class _FakeSourceTrack:
    """Minimal aiortc-style source track feeding the processing pipeline."""

    def __init__(self, frames):
        self._frames = list(frames)
        self.kind = "audio"
        self.label = "test-mic"
        self.readyState = "live"
        self.onended = None
        self.stopped = False

    async def _read(self):
        if not self._frames:
            return None
        return self._frames.pop(0)

    def stop(self):
        self.stopped = True


def _speech_frame(amplitude=0.1):
    return _FakeFrame([amplitude] * FRAME_SAMPLES)


def _silence_frame():
    return _FakeFrame([0.0] * FRAME_SAMPLES)


class TestConfigValidation:
    def test_defaults_are_sane(self):
        cfg = AcousticDefenseConfig()
        assert cfg.enabled is False
        assert cfg.jitter_ms_min <= cfg.jitter_ms_max

    def test_negative_jitter_rejected(self):
        with pytest.raises(ValueError, match="non-negative"):
            AcousticDefenseConfig(jitter_ms_min=-1)

    def test_jitter_min_over_max_rejected(self):
        with pytest.raises(ValueError, match="cannot exceed"):
            AcousticDefenseConfig(jitter_ms_min=20, jitter_ms_max=5)

    def test_negative_silence_frames_rejected(self):
        with pytest.raises(ValueError, match="non-negative"):
            AcousticDefenseConfig(vad_silence_frames=-1)

    def test_negative_transient_rejected(self):
        with pytest.raises(ValueError, match="non-negative"):
            AcousticDefenseConfig(transient_attack_ms=-0.5)

    def test_ratio_bounds(self):
        with pytest.raises(ValueError, match="transient_ratio"):
            AcousticDefenseConfig(transient_ratio=0.0)
        with pytest.raises(ValueError, match="transient_ratio"):
            AcousticDefenseConfig(transient_ratio=1.5)


class TestSpeechPresence:
    def test_empty_is_not_speech(self):
        assert _estimate_speech_presence([], 0.02) is False

    def test_silence_is_not_speech(self):
        assert _estimate_speech_presence([0.0] * 100, 0.02) is False

    def test_loud_is_speech(self):
        assert _estimate_speech_presence([0.1] * 100, 0.02) is True


class TestTransientShaving:
    def test_empty_passthrough(self):
        assert _apply_transient_shaving([], 0.5, 0.5, 0.3) == []

    def test_silence_stays_silent(self):
        out = _apply_transient_shaving([0.0] * 64, 0.5, 0.5, 0.3)
        assert all(abs(s) < 1e-9 for s in out)

    def test_spike_peak_compressed(self):
        samples = [0.0] * 8 + [100.0] + [0.0] * 8
        out = _apply_transient_shaving(samples, 0.2, 0.2, 0.3)
        assert max(abs(s) for s in out) < 100.0
        assert max(abs(s) for s in out) > 0.0

    def test_ratio_one_is_transparent(self):
        samples = [1.0, 2.0, 3.0]
        out = _apply_transient_shaving(samples, 0.5, 0.5, 1.0)
        assert out == [1.0, 2.0, 3.0]


class TestNoiseFrame:
    def test_empty_returns_empty(self):
        assert _generate_noise_frame(48000, 0, 1000, 8000, 0.01) == []

    def test_length_and_amplitude_bounds(self):
        noise = _generate_noise_frame(48000, 256, 1000, 8000, 0.01)
        assert len(noise) == 256
        assert all(abs(s) <= 0.01 + 1e-12 for s in noise)
        assert any(abs(s) > 0 for s in noise)

    def test_band_above_nyquist_is_silent(self):
        # Frequencies above Nyquist (24kHz at 48kHz) leave zero energy.
        noise = _generate_noise_frame(48000, 256, 30000, 40000, 0.01)
        assert all(abs(s) < 1e-12 for s in noise)

    def test_seeded_is_reproducible(self):
        import numpy as np

        with patch.object(np.random, "standard_normal") as mock_normal:
            mock_normal.return_value = np.ones(256)
            first = _generate_noise_frame(48000, 256, 1000, 8000, 0.01)
        with patch.object(np.random, "standard_normal") as mock_normal:
            mock_normal.return_value = np.ones(256)
            second = _generate_noise_frame(48000, 256, 1000, 8000, 0.01)
        assert first == second


class TestAudioProcessingTrack:
    @pytest.mark.asyncio
    async def test_disabled_passthrough(self):
        source = _FakeSourceTrack([_speech_frame()])
        track = AudioProcessingTrack(source, AcousticDefenseConfig(enabled=False))
        out = await track._read()
        assert out is not None
        assert out.data == [0.1] * FRAME_SAMPLES

    @pytest.mark.asyncio
    async def test_properties_proxy_source(self):
        source = _FakeSourceTrack([])
        track = AudioProcessingTrack(source, AcousticDefenseConfig())
        assert track.kind == "audio"
        assert track.label == "test-mic"
        assert track.readyState == "live"
        assert track.enabled is True
        assert track.id
        track.enabled = False
        assert track.enabled is False
        track.stop()
        assert source.stopped is True

    @pytest.mark.asyncio
    async def test_disabled_track_returns_source_frame_unchanged(self):
        source = _FakeSourceTrack([_speech_frame()])
        track = AudioProcessingTrack(source, AcousticDefenseConfig(enabled=True))
        track.enabled = False
        out = await track._read()
        assert out is not None
        assert out.data == [0.1] * FRAME_SAMPLES

    @pytest.mark.asyncio
    async def test_vad_gating_zeroes_sustained_silence(self):
        source = _FakeSourceTrack([_silence_frame(), _silence_frame()])
        cfg = AcousticDefenseConfig(
            enabled=True,
            vad_gating=True,
            vad_silence_frames=1,
            spectral_masking=False,
            transient_shaving=False,
            jitter_ms_min=0,
            jitter_ms_max=0,
        )
        track = AudioProcessingTrack(source, cfg)
        first = await track._read()
        assert first is not None
        assert max(abs(s) for s in first.data) == 0.0
        second = await track._read()
        assert second is not None  # never EOF on silence
        assert all(s == 0.0 for s in second.data)

    @pytest.mark.asyncio
    async def test_speech_resets_silence_counter(self):
        source = _FakeSourceTrack([_silence_frame(), _speech_frame(), _silence_frame()])
        cfg = AcousticDefenseConfig(
            enabled=True,
            vad_gating=True,
            vad_silence_frames=1,
            spectral_masking=False,
            transient_shaving=False,
            jitter_ms_min=0,
            jitter_ms_max=0,
        )
        track = AudioProcessingTrack(source, cfg)
        await track._read()  # silence frame 1 (count 1, not > 1)
        await track._read()  # speech (count reset)
        third = await track._read()  # silence frame again (count 1 again)
        assert third is not None
        assert max(abs(s) for s in third.data) == 0.0

    @pytest.mark.asyncio
    async def test_transient_shaving_reduces_spike(self):
        # A 10x spike exceeds the unit ceiling so downward compression
        # (gain < 1) engages and flattens the peak.
        spike = [0.0] * 100 + [10.0] + [0.0] * 100
        source = _FakeSourceTrack([_FakeFrame(spike)])
        cfg = AcousticDefenseConfig(
            enabled=True,
            vad_gating=False,
            spectral_masking=False,
            transient_shaving=True,
            transient_attack_ms=0.5,
            transient_release_ms=5.0,
            transient_ratio=0.3,
            jitter_ms_min=0,
            jitter_ms_max=0,
        )
        track = AudioProcessingTrack(source, cfg)
        out = await track._read()
        assert out is not None
        peak = max(abs(s) for s in out.data)
        assert peak < 10.0
        assert peak > 0.0

    @pytest.mark.asyncio
    async def test_spectral_masking_adds_noise_to_silence(self):
        source = _FakeSourceTrack([_silence_frame()])
        cfg = AcousticDefenseConfig(
            enabled=True,
            vad_gating=False,
            spectral_masking=True,
            spectral_mask_noise_db=-40.0,
            transient_shaving=False,
            jitter_ms_min=0,
            jitter_ms_max=0,
        )
        track = AudioProcessingTrack(source, cfg)
        out = await track._read()
        assert out is not None
        # Noise amplitude 10^(-40/20) = 0.01; bounded and non-zero.
        assert max(abs(s) for s in out.data) <= 0.01 + 1e-9
        assert any(abs(s) > 0 for s in out.data)

    @pytest.mark.asyncio
    async def test_jitter_shifts_frame(self):
        source = _FakeSourceTrack([_speech_frame()])
        cfg = AcousticDefenseConfig(
            enabled=True,
            vad_gating=False,
            spectral_masking=False,
            transient_shaving=False,
            jitter_ms_min=10,
            jitter_ms_max=10,
        )
        track = AudioProcessingTrack(source, cfg)
        with patch.object(random, "uniform", return_value=10.0):
            out = await track._read()
        assert out is not None
        # 10ms at 48kHz = 480 samples shifted: leading zeros, tail dropped.
        assert out.data[:480] == [0.0] * 480
        assert out.data[480:] == [0.1] * (FRAME_SAMPLES - 480)

    @pytest.mark.asyncio
    async def test_eof_propagates(self):
        source = _FakeSourceTrack([])
        track = AudioProcessingTrack(source, AcousticDefenseConfig(enabled=True))
        assert await track._read() is None
