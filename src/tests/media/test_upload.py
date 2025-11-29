"""
Tests for file upload functionality.
"""

import pytest


@pytest.mark.media
class TestFileUpload:
    """Tests for basic file upload."""

    def test_upload_image_file(self, media_module, user_pool, sample_image_bytes):
        """Test uploading a JPEG image."""
        user = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="test_image.jpg",
            content_type="image/jpeg",
        )
        
        assert result.file_id is not None
        assert result.filename == "test_image.jpg"
        assert result.content_type == "image/jpeg"
        assert result.size == len(sample_image_bytes)
        assert result.url is not None

    def test_upload_png_file(self, media_module, user_pool, sample_png_bytes):
        """Test uploading a PNG image."""
        user = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_png_bytes,
            filename="test_image.png",
            content_type="image/png",
        )
        
        assert result.file_id is not None
        assert result.content_type == "image/png"

    def test_upload_auto_detect_content_type(self, media_module, user_pool, sample_image_bytes):
        """Test content type auto-detection from filename."""
        user = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="auto_detect.jpg",
        )
        
        assert result.content_type == "image/jpeg"

    def test_upload_text_file(self, media_module, user_pool, sample_text_bytes):
        """Test uploading a text file."""
        user = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_text_bytes,
            filename="readme.txt",
            content_type="text/plain",
        )
        
        assert result.file_id is not None
        assert result.content_type == "text/plain"

    def test_upload_pdf_file(self, media_module, user_pool, sample_pdf_bytes):
        """Test uploading a PDF file."""
        user = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_pdf_bytes,
            filename="document.pdf",
            content_type="application/pdf",
        )
        
        assert result.file_id is not None
        assert result.content_type == "application/pdf"

    def test_upload_generates_thumbnails(self, media_module, user_pool, sample_image_bytes):
        """Test that image upload generates thumbnails."""
        user = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="thumb_test.jpg",
            content_type="image/jpeg",
        )
        
        try:
            from PIL import Image
            assert len(result.thumbnails) > 0
        except ImportError:
            assert result.thumbnails == {}

    def test_upload_returns_metadata(self, media_module, user_pool, sample_image_bytes):
        """Test that upload returns image metadata."""
        user = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="meta_test.jpg",
            content_type="image/jpeg",
        )
        
        try:
            from PIL import Image
            assert result.metadata is not None
            assert "width" in result.metadata
            assert "height" in result.metadata
        except ImportError:
            pass


@pytest.mark.media
class TestFileUploadValidation:
    """Tests for upload validation."""

    def test_reject_oversized_file(self, media_module, user_pool, large_file_bytes):
        """Test rejection of files exceeding size limit."""
        user = user_pool.get_user()
        
        with pytest.raises(media_module.FileSizeError) as exc_info:
            media_module.upload_file(
                user_id=user.id,
                file_data=large_file_bytes,
                filename="large.jpg",
                content_type="image/jpeg",
            )
        
        assert exc_info.value.actual_size == len(large_file_bytes)

    def test_reject_disallowed_content_type(self, media_module, user_pool):
        """Test rejection of disallowed content types for specific categories."""
        user = user_pool.get_user()
        
        with pytest.raises(media_module.FileTypeError):
            media_module.upload_file(
                user_id=user.id,
                file_data=b"fake image",
                filename="fake.jpg",
                content_type="image/tiff",
            )


@pytest.mark.media
class TestUploadAttachment:
    """Tests for attachment upload integration."""

    def test_upload_attachment_returns_compatible_data(self, media_module, user_pool, sample_image_bytes):
        """Test that upload_attachment returns messaging-compatible data."""
        user = user_pool.get_user()
        
        attachment = media_module.upload_attachment(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="attachment.jpg",
            content_type="image/jpeg",
        )
        
        assert attachment.filename == "attachment.jpg"
        assert attachment.content_type == "image/jpeg"
        assert attachment.size == len(sample_image_bytes)
        assert attachment.url is not None

    def test_attachment_includes_file_id_in_metadata(self, media_module, user_pool, sample_image_bytes):
        """Test that attachment metadata includes file_id."""
        user = user_pool.get_user()
        
        attachment = media_module.upload_attachment(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="with_meta.jpg",
        )
        
        assert attachment.metadata is not None
        assert "file_id" in attachment.metadata


@pytest.mark.media
class TestFileRetrieval:
    """Tests for file retrieval."""

    def test_get_file_by_id(self, media_module, user_pool, sample_image_bytes):
        """Test retrieving file metadata by ID."""
        user = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="retrieve_test.jpg",
        )
        
        file = media_module.get_file(result.file_id)
        
        assert file is not None
        assert file.id == result.file_id
        assert file.original_filename == "retrieve_test.jpg"
        assert file.uploaded_by == user.id

    def test_get_file_data(self, media_module, user_pool, sample_image_bytes):
        """Test retrieving file data."""
        user = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="data_test.jpg",
        )
        
        data, content_type = media_module.get_file_data(result.file_id)
        
        assert data == sample_image_bytes
        assert content_type == "image/jpeg"

    def test_get_nonexistent_file_returns_none(self, media_module):
        """Test that getting nonexistent file returns None."""
        file = media_module.get_file(999999999)
        assert file is None


@pytest.mark.media
class TestFileDelete:
    """Tests for file deletion."""

    def test_delete_own_file(self, media_module, user_pool, sample_image_bytes):
        """Test deleting own file."""
        user = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="delete_test.jpg",
        )
        
        deleted = media_module.delete_file(user.id, result.file_id)
        assert deleted is True
        
        file = media_module.get_file(result.file_id)
        assert file is None

    def test_cannot_delete_others_file(self, media_module, user_pool, sample_image_bytes):
        """Test that users cannot delete others' files."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()
        
        result = media_module.upload_file(
            user_id=user1.id,
            file_data=sample_image_bytes,
            filename="others_file.jpg",
        )
        
        with pytest.raises(media_module.PermissionDeniedError):
            media_module.delete_file(user2.id, result.file_id)

    def test_delete_nonexistent_file_returns_false(self, media_module, user_pool):
        """Test deleting nonexistent file returns False."""
        user = user_pool.get_user()
        deleted = media_module.delete_file(user.id, 999999999)
        assert deleted is False
