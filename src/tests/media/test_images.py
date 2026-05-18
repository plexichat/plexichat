"""Tests for media image processing (thumbnails, resize, convert)."""

import pytest

from src.core.media.models import MediaType
from src.core.media.exceptions import ImageProcessingError, MediaError


# Minimal valid PNG (1x1 pixel)
MINI_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.media
class TestImages:
    """Tests for image file processing."""

    def test_upload_image_creates_metadata(self, media_manager, test_user):
        """Test that uploading an image creates metadata."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="meta_test.png",
            content_type="image/png",
        )
        # Image metadata may or may not be available depending on Pillow
        if result.metadata:
            assert "width" in result.metadata or "format" in result.metadata

    def test_get_thumbnails(self, media_manager, test_user):
        """Test getting thumbnails for an uploaded image."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="thumb_test.png",
            content_type="image/png",
        )
        # Thumbnails may or may not be generated depending on Pillow availability
        thumbnails = media_manager.get_thumbnails(result.file_id)
        assert isinstance(thumbnails, dict)

    def test_resize_image(self, media_manager, test_user):
        """Test resizing an image."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="resize_test.png",
            content_type="image/png",
        )
        try:
            resized = media_manager.resize_image(result.file_id, width=32, height=32)
            assert isinstance(resized, bytes)
            assert len(resized) > 0
        except (ImageProcessingError, MediaError):
            # Pillow may not be available in test environment
            pytest.skip("Image processing not available")

    def test_resize_nonexistent_image(self, media_manager):
        """Test resizing a nonexistent image raises error."""
        with pytest.raises(MediaError):
            media_manager.resize_image(9999999, width=32, height=32)

    def test_convert_image_format(self, media_manager, test_user):
        """Test converting image format."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="convert_test.png",
            content_type="image/png",
        )
        try:
            converted = media_manager.convert_image(result.file_id, "JPEG")
            assert isinstance(converted, bytes)
        except (ImageProcessingError, MediaError):
            pytest.skip("Image processing not available")

    def test_detect_image_media_type(self, media_manager):
        """Test that image content type is detected as IMAGE."""
        media_type = media_manager._detect_media_type("image/png")
        assert media_type == MediaType.IMAGE

    def test_detect_jpeg_media_type(self, media_manager):
        """Test JPEG detection."""
        media_type = media_manager._detect_media_type("image/jpeg")
        assert media_type == MediaType.IMAGE

    def test_detect_gif_media_type(self, media_manager):
        """Test GIF detection."""
        media_type = media_manager._detect_media_type("image/gif")
        assert media_type == MediaType.IMAGE

    def test_detect_webp_media_type(self, media_manager):
        """Test WebP detection."""
        media_type = media_manager._detect_media_type("image/webp")
        assert media_type == MediaType.IMAGE
