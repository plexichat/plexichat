"""Regression tests for media compression capability reporting."""

import pytest


@pytest.mark.media
class TestCompressionStatus:
    """Compression status should accurately expose optional dependencies."""

    def test_configured_ffprobe_path_is_reported(self, monkeypatch):
        """A configured executable is preferred over PATH discovery."""
        from src.core.media import compression

        configured_path = "/opt/plexichat/bin/ffprobe"
        media_config = {
            "compression": {"enabled": False},
            "video_processing": {"ffprobe_path": configured_path},
        }

        def fake_config_get(key, default=None):
            return media_config if key == "media" else default

        def fake_which(candidate):
            if candidate == configured_path:
                return configured_path
            return None

        monkeypatch.setattr(compression.config, "get", fake_config_get)
        monkeypatch.setattr(compression.shutil, "which", fake_which)

        manager = compression.CompressionManager()

        assert manager.get_status()["ffprobe_available"] is True
        assert manager.get_status()["ffprobe_path"] == configured_path

    def test_processor_falls_back_when_configured_path_is_invalid(self, monkeypatch):
        """Status and metadata processing must resolve the same executable."""
        from src.core.media.processing import video

        configured_path = "/missing/ffprobe"
        fallback_path = "/usr/bin/ffprobe"
        media_config = {
            "video_processing": {"ffprobe_path": configured_path},
        }

        def fake_config_get(key, default=None):
            return media_config if key == "media" else default

        def fake_which(candidate):
            return None if candidate == configured_path else fallback_path

        monkeypatch.setattr(video.config, "get", fake_config_get)
        monkeypatch.setattr(video.shutil, "which", fake_which)

        processor = video.VideoProcessor()

        assert processor.is_available() is True
        assert processor._ffprobe_path == fallback_path

    def test_new_dependency_fields_are_backward_compatible(self):
        """Existing direct model consumers may omit newly added fields."""
        from src.api.schemas.media import CompressionStatusResponse

        status = CompressionStatusResponse(
            enabled=False,
            image_compression=False,
            video_compression=False,
        )

        assert status.pillow_available is False
        assert status.ffmpeg_available is False
        assert status.ffprobe_available is False
        assert "ffprobe_path" not in type(status).model_fields
