"""Tests for media upload functionality."""

import pytest
from unittest.mock import patch
import io
from PIL import Image


class TestMediaUpload:
    """Test media upload operations."""

    def test_upload_image(self, db, auth_manager, media_manager):
        """Test uploading an image."""
        from src.utils import encryption

        # Create user
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        # Create a valid PNG image using PIL
        img = Image.new("RGB", (10, 10), color="red")
        img_bytes = io.BytesIO()
        img.save(img_bytes, format="PNG")
        image_data = img_bytes.getvalue()

        # Upload the image
        result = media_manager.upload_file(
            user_id=user.id,
            file_data=image_data,
            filename="test_image.png",
            content_type="image/png",
        )

        assert result is not None
        assert result.file_id is not None
        assert result.url is not None
        # Size may differ due to compression/processing
        assert result.size > 0

    def test_upload_invalid_file_type(self, db, auth_manager, media_manager):
        """Test uploading invalid file type fails."""
        from src.utils import encryption
        from src.core.media.exceptions import FileTypeError

        # Create user
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        # Create fake file data with blocked extension
        file_data = b"fake_data" * 100

        # Try to upload invalid file type
        with pytest.raises(FileTypeError):
            media_manager.upload_file(
                user_id=user.id,
                file_data=file_data,
                filename="test.exe",
                content_type="application/x-msdownload",
            )

    def test_get_media(self, db, auth_manager, media_manager):
        """Test retrieving media info."""
        from src.utils import encryption

        # Create user
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        # Upload a file first
        img = Image.new("RGB", (10, 10), color="red")
        img_bytes = io.BytesIO()
        img.save(img_bytes, format="PNG")
        image_data = img_bytes.getvalue()

        upload_result = media_manager.upload_file(
            user_id=user.id,
            file_data=image_data,
            filename="test_image.png",
            content_type="image/png",
        )

        # Retrieve the media info
        media_info = media_manager.get_file(upload_result.file_id)

        assert media_info is not None
        assert media_info.id == upload_result.file_id
        assert media_info.filename.endswith(".png")
        # Content type may be converted during processing
        assert media_info.content_type.startswith("image/")
