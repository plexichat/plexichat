"""Regression tests for combined-segment recording output (L12).

When multiple SFU segments are concatenated with ffmpeg, the output file must
use the container and audio codec of the configured recording format; it is
uploaded afterwards under that format's extension and content type. The old
code always wrote a ``.webm``/``libopus`` file regardless of configuration.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from src.core.artifacts.recording import (
    RecordingManager,
    combine_output_target,
)


def test_combine_output_target_defaults_to_webm():
    assert combine_output_target("not-a-format") == (".webm", "libopus")
    assert combine_output_target("") == (".webm", "libopus")


def test_combine_output_target_follows_configured_format():
    assert combine_output_target("webm") == (".webm", "libopus")
    assert combine_output_target("opus") == (".ogg", "libopus")
    assert combine_output_target("wav") == (".wav", "pcm_s16le")
    assert combine_output_target("mp4") == (".mp4", "aac")


def _recorder_with_format(recording_format: str) -> RecordingManager:
    recorder = RecordingManager.__new__(RecordingManager)
    recorder._recording_format = recording_format
    return recorder


def test_try_combine_writes_configured_container_and_codec(tmp_path, monkeypatch):
    """The ffmpeg command emitted by _try_combine matches the format."""
    segments = []
    for i in range(2):
        seg = tmp_path / f"seg_{i}.webm"
        seg.write_bytes(b"not really audio")
        segments.append(str(seg))

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["ffmpeg", "-version"]:
            return SimpleNamespace(returncode=0)
        # ffmpeg "combine" success: produce the output file named at cmd[-1].
        Path(cmd[-1]).write_bytes(b"combined")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("src.core.artifacts.recording.subprocess.run", fake_run)

    recorder = _recorder_with_format("wav")
    result = asyncio.run(recorder._try_combine(segments))

    assert result is not None
    assert result.endswith(".wav")
    combine_cmd = next(c for c in calls if c[:2] == ["ffmpeg", "-y"])
    assert combine_cmd[-2] == "pcm_s16le"
    assert combine_cmd[-1].endswith(".wav")
    Path(result).unlink(missing_ok=True)


def test_try_combine_keeps_webm_default_unchanged(tmp_path, monkeypatch):
    """Default (webm) format keeps the historical libopus/.webm output."""
    segments = []
    for i in range(2):
        seg = tmp_path / f"seg_{i}.webm"
        seg.write_bytes(b"x")
        segments.append(str(seg))

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["ffmpeg", "-version"]:
            return SimpleNamespace(returncode=0)
        Path(cmd[-1]).write_bytes(b"combined")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("src.core.artifacts.recording.subprocess.run", fake_run)

    recorder = _recorder_with_format("webm")
    result = asyncio.run(recorder._try_combine(segments))

    assert result is not None
    assert result.endswith(".webm")
    combine_cmd = next(c for c in calls if c[:2] == ["ffmpeg", "-y"])
    assert combine_cmd[-2] == "libopus"
    assert combine_cmd[-1].endswith(".webm")
    Path(result).unlink(missing_ok=True)
