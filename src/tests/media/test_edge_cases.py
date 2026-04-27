"""Tests for media edge cases and error handling."""

import pytest

from src.core.media.models import MediaType
from src.core.media.exceptions import (
    FileSizeError,
    FileTypeError,
    MediaError,
)


MINI_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.media
class TestEdgeCases:
    """Tests for media edge cases and boundary conditions."""

    def test_upload_oversized_file(self, media_manager, test_user):
        """Test that files exceeding size limit are rejected."""
        with pytest.raises(FileSizeError):
            media_manager.upload_file(
                user_id=test_user.id,
                file_data=b"\x00" * (20 * 1024 * 1024),  # 20MB
                filename="big.png",
                content_type="image/png",
            )

    def test_upload_disallowed_content_type(self, media_manager, test_user):
        """Test that disallowed content types are rejected."""
        with pytest.raises(FileTypeError):
            media_manager.upload_file(
                user_id=test_user.id,
                file_data=MINI_PNG,
                filename="test.tiff",
                content_type="image/tiff",
            )

    def test_upload_empty_filename(self, media_manager, test_user):
        """Test uploading with empty filename gets a default name."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="",
            content_type="image/png",
        )
        assert result.filename is not None
        assert len(result.filename) > 0

    def test_upload_with_special_chars_filename(self, media_manager, test_user):
        """Test uploading with special characters in filename."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="test file (1).png",
            content_type="image/png",
        )
        assert result is not None

    def test_get_file_data_nonexistent(self, media_manager):
        """Test getting file data for nonexistent file raises error."""
        with pytest.raises(MediaError):
            media_manager.get_file_data(9999999)

    def test_detect_media_type_for_video(self, media_manager):
        """Test media type detection for video."""
        assert media_manager._detect_media_type("video/mp4") == MediaType.VIDEO

    def test_detect_media_type_for_audio(self, media_manager):
        """Test media type detection for audio."""
        assert media_manager._detect_media_type("audio/mpeg") == MediaType.AUDIO

    def test_detect_media_type_for_document(self, media_manager):
        """Test media type detection for documents."""
        assert media_manager._detect_media_type("application/pdf") == MediaType.DOCUMENT

    def test_detect_media_type_for_other(self, media_manager):
        """Test media type detection for unknown types."""
        assert (
            media_manager._detect_media_type("application/unknown") == MediaType.OTHER
        )

    def test_upload_same_file_twice(self, media_manager, test_user):
        """Test uploading the same file twice produces different IDs."""
        result1 = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="dup1.png",
            content_type="image/png",
        )
        result2 = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="dup2.png",
            content_type="image/png",
        )
        assert result1.file_id != result2.file_id

    def test_upload_text_file(self, media_manager, test_user):
        """Test uploading a text file."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=b"Hello, world!",
            filename="test.txt",
            content_type="text/plain",
        )
        assert result.file_id is not None
        assert result.content_type == "text/plain"
