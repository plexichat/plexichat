"""Regression tests for the video metadata size cap on the file path.

``VideoProcessor.get_metadata_from_bytes`` enforced
``max_size_for_metadata`` before running ffprobe, but the temp-file variant
``get_metadata`` did not. The upload pipeline only uses the bytes path for
files up to the in-memory threshold, so every larger upload reached the
unguarded method and the configured cap was silently ignored for exactly the
files it exists to bound.
"""

import pytest

from src.core.media.exceptions import VideoProcessingError
from src.core.media.processing import video


@pytest.fixture
def _isolated_video_config(monkeypatch):
    """Keep ``VideoProcessor`` construction off the real app config."""
    media_config = {"video_processing": {}}

    def fake_config_get(key, default=None):
        return media_config if key == "media" else default

    monkeypatch.setattr(video.config, "get", fake_config_get)


def _processor(max_size: int) -> video.VideoProcessor:
    """A processor with a forced ffprobe path and an explicit size cap."""
    proc = video.VideoProcessor(max_size_for_metadata=max_size)
    # Force a path so a missing ffprobe is never the reason a call is
    # rejected — the size guard must be what fires.
    proc._ffprobe_path = "/usr/bin/ffprobe"
    return proc


@pytest.mark.media
class TestVideoMetadataSizeLimit:
    def test_oversized_file_is_rejected_without_invoking_ffprobe(
        self, tmp_path, monkeypatch, _isolated_video_config
    ):
        """A file over the cap must fail closed *before* ffprobe runs."""
        oversize = tmp_path / "oversize.mp4"
        oversize.write_bytes(b"x" * 4096)

        calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            raise AssertionError("ffprobe must not run for an over-cap file")

        monkeypatch.setattr(video.subprocess, "run", fake_run)

        proc = _processor(max_size=1024)
        with pytest.raises(VideoProcessingError):
            proc.get_metadata(str(oversize))

        assert calls == [], "ffprobe was invoked despite the file exceeding the cap"

    def test_file_within_the_cap_reaches_ffprobe(
        self, tmp_path, monkeypatch, _isolated_video_config
    ):
        """Non-vacuity: an under-cap file still reaches ffprobe."""
        small = tmp_path / "small.mp4"
        small.write_bytes(b"x" * 100)

        calls = []

        class _Result:
            returncode = 1  # drive ffprobe's own error path
            stdout = b""
            stderr = b"ffprobe boom"

        def fake_run(*args, **kwargs):
            calls.append(args)
            return _Result()

        monkeypatch.setattr(video.subprocess, "run", fake_run)

        proc = _processor(max_size=1024)
        with pytest.raises(VideoProcessingError):
            proc.get_metadata(str(small))

        assert calls, "an under-cap file must still reach ffprobe"
