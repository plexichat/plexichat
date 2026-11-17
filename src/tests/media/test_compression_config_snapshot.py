"""Compression parameters are bounded once, at construction.

Before: every compress() re-parsed and re-validated config values (quality,
max_dimension, preset, crf, audio_bitrate). After: the bounded values are
snapshotted at init, invalid config fails fast, and the ffmpeg command is
built from the *bounded* audio bitrate rather than the raw config string.
"""

import io
from types import SimpleNamespace

import pytest

from src.core.media import compression
from src.core.media.compression import (
    CompressionManager,
    ImageCompressor,
    VideoCompressor,
)
from src.core.security.hardening import HardeningViolation


def _patch_config(monkeypatch, media_config, calls):
    def fake_get(key, default=None):
        calls.append(key)
        return media_config if key == "media" else default

    monkeypatch.setattr(compression.config, "get", fake_get)


def test_image_params_are_bounded_once_and_not_reread(monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    calls = []
    _patch_config(
        monkeypatch,
        {
            "compression": {
                "enabled": True,
                "preserve_original": True,
                "image": {"format": "jpeg", "quality": 77, "max_dimension": 50},
            }
        },
        calls,
    )

    compressor = ImageCompressor()
    assert compressor._quality == 77
    assert compressor._max_dimension == 50
    assert compressor._format == "jpeg"
    config_calls = len(calls)

    image = Image.new("RGB", (16, 16), color="green")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    data = buffer.getvalue()
    assert compressor.compress(data).success
    assert compressor.compress(data).success

    assert len(calls) == config_calls


def test_invalid_image_bounds_fail_at_init(monkeypatch):
    calls = []
    for bad in ({"quality": 999}, {"max_dimension": 10**9}):
        image_cfg = {"format": "webp"}
        image_cfg.update(bad)
        _patch_config(monkeypatch, {"compression": {"image": image_cfg}}, calls)
        with pytest.raises(HardeningViolation):
            ImageCompressor()


def test_bounded_audio_bitrate_reaches_ffmpeg_command(monkeypatch):
    calls = []
    cmds = []
    _patch_config(
        monkeypatch,
        {
            "compression": {
                "video": {
                    "enabled": True,
                    "codec": "h264",
                    "crf": 20,
                    "preset": "fast",
                    "audio_bitrate": "192k",
                }
            }
        },
        calls,
    )
    monkeypatch.setattr(compression.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

    def fake_run(cmd, *args, **kwargs):
        cmds.append(list(cmd))
        if "-version" in cmd:
            return SimpleNamespace(returncode=0, stderr=b"")
        with open(cmd[-1], "wb") as handle:
            handle.write(b"encoded-video")
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(compression.subprocess, "run", fake_run)

    compressor = VideoCompressor()
    assert compressor._audio_bitrate == "192k"
    assert compressor._preset == "fast"
    assert compressor._crf == 20
    config_calls = len(calls)

    result = compressor.compress(b"video-one")
    assert result.success
    transcode_cmd = next(cmd for cmd in cmds if "-version" not in cmd)
    assert transcode_cmd[transcode_cmd.index("-b:a") + 1] == "192k"
    assert transcode_cmd[transcode_cmd.index("-preset") + 1] == "fast"

    # A second, different payload must not reread config either.
    assert compressor.compress(b"video-two").success
    assert len(calls) == config_calls


def test_invalid_audio_bitrate_fails_at_init(monkeypatch):
    calls = []
    _patch_config(
        monkeypatch,
        {"compression": {"video": {"audio_bitrate": "192k; rm -rf /"}}},
        calls,
    )
    monkeypatch.setattr(compression.shutil, "which", lambda _name: None)
    monkeypatch.setattr(compression.os.path, "exists", lambda _path: False)

    with pytest.raises(HardeningViolation):
        VideoCompressor()


def test_manager_enabled_flag_is_snapshotted(monkeypatch):
    calls = []
    media_config = {"compression": {"enabled": False}}
    _patch_config(monkeypatch, media_config, calls)

    manager = CompressionManager()
    assert manager.is_enabled() is False
    media_config["compression"]["enabled"] = True
    assert manager.is_enabled() is False
