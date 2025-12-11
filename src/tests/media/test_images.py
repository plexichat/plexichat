"""
Tests for image processing functionality.
"""

import pytest


def pillow_available():
    """Check if Pillow is available."""
    try:
        from PIL import Image
        return True
    except ImportError:
        return False


@pytest.mark.media
@pytest.mark.pillow
@pytest.mark.skipif(not pillow_available(), reason="Pillow not installed")
class TestImageProcessor:
    """Tests for ImageProcessor class."""

    def test_get_image_metadata(self, sample_image_bytes):
        """Test extracting image metadata."""
        from src.core.media.processing.images import ImageProcessor

        processor = ImageProcessor()
        metadata = processor.get_metadata(sample_image_bytes)

        assert metadata.width > 0
        assert metadata.height > 0
        assert metadata.format in ("JPEG", "PNG", "GIF", "WEBP")

    def test_get_png_metadata(self, sample_png_bytes):
        """Test extracting PNG metadata."""
        from src.core.media.processing.images import ImageProcessor

        processor = ImageProcessor()
        metadata = processor.get_metadata(sample_png_bytes)

        assert metadata.format == "PNG"
        assert metadata.has_alpha is True

    def test_create_thumbnail(self, sample_image_bytes):
        """Test creating a single thumbnail."""
        from src.core.media.processing.images import ImageProcessor

        processor = ImageProcessor()
        thumb_data, width, height = processor.create_thumbnail(
            sample_image_bytes, size=64
        )

        assert len(thumb_data) > 0
        assert width <= 64
        assert height <= 64

    def test_create_multiple_thumbnails(self, sample_image_bytes):
        """Test creating thumbnails at multiple sizes."""
        from src.core.media.processing.images import ImageProcessor

        processor = ImageProcessor()
        results = processor.create_thumbnails(
            sample_image_bytes, sizes=[64, 128, 256]
        )

        assert 64 in results
        assert 128 in results
        assert 256 in results

    def test_resize_image_by_width(self, sample_image_bytes):
        """Test resizing image by width."""
        from src.core.media.processing.images import ImageProcessor

        processor = ImageProcessor()
        resized, width, height = processor.resize(sample_image_bytes, width=50)

        assert width == 50
        assert len(resized) > 0

    def test_resize_image_by_height(self, sample_image_bytes):
        """Test resizing image by height."""
        from src.core.media.processing.images import ImageProcessor

        processor = ImageProcessor()
        resized, width, height = processor.resize(sample_image_bytes, height=50)

        assert height == 50
        assert len(resized) > 0

    def test_resize_maintains_aspect_ratio(self, sample_image_bytes):
        """Test that resize maintains aspect ratio by default."""
        from src.core.media.processing.images import ImageProcessor
        from PIL import Image
        import io

        processor = ImageProcessor()

        original = Image.open(io.BytesIO(sample_image_bytes))
        orig_ratio = original.width / original.height

        resized, width, height = processor.resize(sample_image_bytes, width=50)
        new_ratio = width / height

        assert abs(orig_ratio - new_ratio) < 0.1

    def test_convert_to_webp(self, sample_image_bytes):
        """Test converting image to WebP format."""
        from src.core.media.processing.images import ImageProcessor
        from PIL import Image
        import io

        processor = ImageProcessor()
        converted = processor.convert_format(sample_image_bytes, "WEBP")

        img = Image.open(io.BytesIO(converted))
        assert img.format == "WEBP"

    def test_convert_to_png(self, sample_image_bytes):
        """Test converting image to PNG format."""
        from src.core.media.processing.images import ImageProcessor
        from PIL import Image
        import io

        processor = ImageProcessor()
        converted = processor.convert_format(sample_image_bytes, "PNG")

        img = Image.open(io.BytesIO(converted))
        assert img.format == "PNG"

    def test_strip_metadata(self, sample_image_bytes):
        """Test stripping EXIF metadata."""
        from src.core.media.processing.images import ImageProcessor

        processor = ImageProcessor()
        stripped = processor.strip_metadata(sample_image_bytes)

        assert len(stripped) > 0

    def test_is_supported_content_type(self):
        """Test content type support checking."""
        from src.core.media.processing.images import ImageProcessor

        processor = ImageProcessor()

        assert processor.is_supported("image/jpeg") is True
        assert processor.is_supported("image/png") is True
        assert processor.is_supported("image/gif") is True
        assert processor.is_supported("image/webp") is True
        assert processor.is_supported("video/mp4") is False
        assert processor.is_supported("text/plain") is False


@pytest.mark.media
@pytest.mark.pillow
@pytest.mark.skipif(not pillow_available(), reason="Pillow not installed")
class TestImageProcessingIntegration:
    """Integration tests for image processing via media module."""

    def test_upload_generates_thumbnails(self, media_module, user_pool, sample_image_bytes):
        """Test that uploading an image generates thumbnails."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="thumb_gen.jpg",
        )

        assert len(result.thumbnails) > 0
        assert 64 in result.thumbnails or 128 in result.thumbnails

    def test_get_thumbnails_for_file(self, media_module, user_pool, sample_image_bytes):
        """Test retrieving thumbnails for a file."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="get_thumbs.jpg",
        )

        thumbnails = media_module.get_thumbnails(result.file_id)

        assert isinstance(thumbnails, dict)

    def test_create_custom_thumbnail(self, media_module, user_pool, sample_image_bytes):
        """Test creating a thumbnail at custom size."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="custom_thumb.jpg",
        )

        url = media_module.create_thumbnail(result.file_id, size=200)

        assert url is not None

    def test_resize_uploaded_image(self, media_module, user_pool, sample_image_bytes):
        """Test resizing an uploaded image."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="resize_me.jpg",
        )

        resized = media_module.resize_image(result.file_id, width=50)

        assert len(resized) > 0
        assert len(resized) < len(sample_image_bytes)

    def test_convert_uploaded_image(self, media_module, user_pool, sample_image_bytes):
        """Test converting an uploaded image format."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="convert_me.jpg",
        )

        converted = media_module.convert_image(result.file_id, "PNG")

        assert len(converted) > 0
        assert converted[:8] == b'\x89PNG\r\n\x1a\n'

    def test_upload_extracts_dimensions(self, media_module, user_pool, sample_image_bytes):
        """Test that upload extracts image dimensions."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="dimensions.jpg",
        )

        assert result.metadata is not None
        assert "width" in result.metadata
        assert "height" in result.metadata
        assert result.metadata["width"] > 0
        assert result.metadata["height"] > 0
