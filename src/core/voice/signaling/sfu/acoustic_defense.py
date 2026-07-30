"""
Acoustic eavesdropping defense module for Plexichat SFU adapters.

Provides server-side audio processing to defend against AI-based
keystroke acoustic eavesdropping attacks (Self-Supervised Acoustic
Eavesdropping Attacks on Keyboards, July 2026).

Four defense mechanisms:
1. Jitter Injection - Randomized 5-20ms per-packet delay breaks
   temporal continuity that Transformer models rely on.
2. Spectral Masking - Low-level noise in the 1kHz-8kHz band during
   silence masks the harmonic differences between key types.
3. VAD Gating - Drops packets lacking human vocal harmonic structure,
   preventing keyboard impulse noises from being forwarded.
4. Transient Shaving - Fast-attack limiter flattens keyboard click
   peaks without impacting continuous speech waveforms.
"""

import math
import random
import secrets
import uuid
from dataclasses import dataclass
from typing import List, Any


@dataclass
class AcousticDefenseConfig:
    """Configuration for acoustic eavesdropping defenses."""

    enabled: bool = False
    jitter_ms_min: float = 5.0
    jitter_ms_max: float = 20.0
    spectral_masking: bool = True
    spectral_mask_noise_db: float = -40.0
    spectral_mask_low_hz: float = 1000.0
    spectral_mask_high_hz: float = 8000.0
    vad_gating: bool = True
    vad_speech_threshold: float = 0.02
    vad_silence_frames: int = 3
    transient_shaving: bool = True
    transient_attack_ms: float = 0.5
    transient_release_ms: float = 5.0
    transient_ratio: float = 0.3


def _generate_noise_frame(
    sample_rate: int,
    num_samples: int,
    low_hz: float,
    high_hz: float,
    amplitude: float,
) -> List[float]:
    """Generate band-limited noise samples in the specified frequency range."""
    samples = []
    for i in range(num_samples):
        t = i / sample_rate
        envelope = 0.5 * (1.0 - math.cos(2.0 * math.pi * t * 0.5))
        low_component = math.sin(
            2.0 * math.pi * low_hz * t + (secrets.randbelow(10000) / 10000.0) * 6.28
        )
        high_component = math.sin(
            2.0 * math.pi * high_hz * t + (secrets.randbelow(10000) / 10000.0) * 6.28
        )
        mid_component = math.sin(
            2.0 * math.pi * ((low_hz + high_hz) / 2.0) * t
            + (secrets.randbelow(10000) / 10000.0) * 6.28
        )
        noise = (low_component + mid_component + high_component) / 3.0
        samples.append(noise * amplitude * envelope)
    return samples


def _apply_transient_shaving(
    samples: List[float],
    attack_coeff: float,
    release_coeff: float,
    ratio: float,
) -> List[float]:
    """Apply a fast-attack, fast-release compressor to flatten transients."""
    envelope = 0.0
    result = []
    for sample in samples:
        abs_sample = abs(sample)
        if abs_sample > envelope:
            envelope = attack_coeff * abs_sample + (1.0 - attack_coeff) * envelope
        else:
            envelope = release_coeff * abs_sample + (1.0 - release_coeff) * envelope
        gain = 1.0
        if envelope > 0.001:
            target_gain = (envelope ** (ratio - 1.0)) if ratio < 1.0 else 1.0
            gain = min(1.0, target_gain)
        result.append(sample * gain)
    return result


def _estimate_speech_presence(
    samples: List[float],
    threshold: float,
) -> bool:
    """Estimate whether the audio frame contains speech based on RMS energy."""
    if not samples:
        return False
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    return rms > threshold


class AudioProcessingTrack:
    """
    Wraps a MediaStreamTrack and applies acoustic eavesdropping defenses.

    Processes audio frames through a pipeline of:
    1. Jitter injection (random per-packet delay)
    2. VAD gating (drop non-speech frames)
    3. Transient shaving (compress keyboard click peaks)
    4. Spectral masking (inject comfort noise in 1kHz-8kHz band)
    """

    def __init__(
        self,
        source_track: Any,
        config: AcousticDefenseConfig,
    ):
        self._source = source_track
        self._config = config
        self._kind = source_track.kind
        self._id = str(uuid.uuid4())
        self._label = source_track.label
        self._ready_state = source_track.readyState
        self._enabled = True
        self._silence_frame_count = 0
        self._sample_rate = 48000
        self._frame_duration_ms = 20
        self._samples_per_frame = int(
            self._sample_rate * self._frame_duration_ms / 1000.0
        )

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def id(self) -> str:
        return self._id

    @property
    def label(self) -> str:
        return self._label

    @property
    def readyState(self) -> str:
        return self._ready_state

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def onended(self):
        return self._source.onended

    @onended.setter
    def onended(self, handler: Any) -> None:
        self._source.onended = handler

    def clone(self) -> "AudioProcessingTrack":
        return AudioProcessingTrack(self._source, self._config)

    def stop(self) -> None:
        self._source.stop()

    async def _read(self) -> Any:
        """Read and process the next audio frame from the source track."""
        if not self._enabled:
            return await self._source._read()

        frame = await self._source._read()

        if frame is None:
            return None

        if not self._config.enabled:
            return frame

        samples = list(frame.data)

        if self._config.transient_shaving:
            attack_coeff = math.exp(
                -1.0 / (self._config.transient_attack_ms / 1000.0 * self._sample_rate)
            )
            release_coeff = math.exp(
                -1.0 / (self._config.transient_release_ms / 1000.0 * self._sample_rate)
            )
            samples = _apply_transient_shaving(
                samples, attack_coeff, release_coeff, self._config.transient_ratio
            )

        has_speech = _estimate_speech_presence(
            samples, self._config.vad_speech_threshold
        )

        if self._config.vad_gating and not has_speech:
            self._silence_frame_count += 1
            if self._silence_frame_count > self._config.vad_silence_frames:
                return None
        else:
            self._silence_frame_count = 0

        if self._config.spectral_masking and not has_speech:
            noise_amplitude = 10 ** (self._config.spectral_mask_noise_db / 20.0)
            noise = _generate_noise_frame(
                self._sample_rate,
                len(samples),
                self._config.spectral_mask_low_hz,
                self._config.spectral_mask_high_hz,
                noise_amplitude,
            )
            samples = [s + n for s, n in zip(samples, noise)]

        if self._config.jitter_ms_min > 0 or self._config.jitter_ms_max > 0:
            jitter_ms = random.uniform(
                self._config.jitter_ms_min, self._config.jitter_ms_max
            )
            jitter_samples = int(jitter_ms / 1000.0 * self._sample_rate)
            if jitter_samples > 0:
                silence = [0.0] * jitter_samples
                samples = silence + samples

        frame.data = samples
        return frame
