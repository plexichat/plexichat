"""Regression: compression must not be a resource-exhaustion primitive.

Two unbounded reads were left after the ffmpeg-argument/temp-path hardening
waves:

* ``ImageCompressor.compress`` called ``thumbnail()`` on any image, letting a
  header that declares a huge pixel count force a multi-gigabyte decode.
* ``VideoCompressor._compress_uncached`` called ``f.read()`` on the ffmpeg
  output file with no size cap, so a forged input that makes ffmpeg emit a
  huge file pinned all of it in memory.
"""

import io
from types import SimpleNamespace

import pytest

from src.core.media import compression
from src.core.media.compression import ImageCompressor, VideoCompressor


def _patch_media(monkeypatch, media_config):
    def fake_get(key, default=None):
        return media_config if key == "media" else default

    monkeypatch.setattr(compression.config, "get", fake_get)


def test_image_decompression_bomb_rejected_before_decode(monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    _patch_media(
        monkeypatch,
        {
            "compression": {
                "enabled": True,
                "image": {
                    "format": "png",
                    "quality": 85,
                    "max_dimension": 4096,
                    # Deliberately tiny so a 16x16 image (256 px) is a "bomb".
                    "max_pixels": 100,
                },
            }
        },
    )
    compressor = ImageCompressor()
    assert compressor._max_pixels == 100

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color="green").save(buf, format="PNG")

    result = compressor.compress(buf.getvalue())
    assert result.success is False
    assert "decompression" in (result.error or "").lower()


def test_image_within_pixel_limit_still_compresses(monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    _patch_media(
        monkeypatch,
        {
            "compression": {
                "enabled": True,
                "preserve_original": True,
                "image": {
                    "format": "png",
                    "quality": 85,
                    "max_dimension": 4096,
                    "max_pixels": 10_000,
                },
            }
        },
    )
    compressor = ImageCompressor()

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color="green").save(buf, format="PNG")

    result = compressor.compress(buf.getvalue())
    assert result.success is True
    assert result.data is not None


def test_ffmpeg_output_read_is_size_bounded(monkeypatch):
    _patch_media(
        monkeypatch,
        {
            "compression": {
                "video": {
                    "enabled": True,
                    "codec": "h264",
                    "crf": 20,
                    "preset": "fast",
                    "audio_bitrate": "128k",
                }
            }
        },
    )
    monkeypatch.setattr(compression.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

    def fake_run(cmd, *args, **kwargs):
        if "-version" in cmd:
            return SimpleNamespace(returncode=0, stderr=b"")
        # Emit more bytes than the (test-lowered) cap.
        with open(cmd[-1], "wb") as handle:
            handle.write(b"x" * 256)
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(compression.subprocess, "run", fake_run)
    # Lower the cap so the test does not need to write hundreds of MiB.
    monkeypatch.setattr(compression, "MAX_TRANSCODE_OUTPUT_BYTES", 128)

    compressor = VideoCompressor()
    result = compressor.compress(b"video-one")

    assert result.success is False
    assert result.data is None
    assert "exceeds size limit" in (result.error or "").lower()


def test_ffmpeg_output_within_cap_still_succeeds(monkeypatch):
    _patch_media(
        monkeypatch,
        {
            "compression": {
                "video": {
                    "enabled": True,
                    "codec": "h264",
                    "crf": 20,
                    "preset": "fast",
                    "audio_bitrate": "128k",
                }
            }
        },
    )
    monkeypatch.setattr(compression.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

    def fake_run(cmd, *args, **kwargs):
        if "-version" in cmd:
            return SimpleNamespace(returncode=0, stderr=b"")
        with open(cmd[-1], "wb") as handle:
            handle.write(b"encoded-video")
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(compression.subprocess, "run", fake_run)
    monkeypatch.setattr(compression, "MAX_TRANSCODE_OUTPUT_BYTES", 128)

    compressor = VideoCompressor()
    result = compressor.compress(b"video-two")

    assert result.success is True
    assert result.data == b"encoded-video"
