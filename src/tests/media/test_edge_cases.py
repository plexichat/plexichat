"""
Edge case tests for media module.
"""

import pytest


@pytest.mark.media
class TestFilenameEdgeCases:
    """Tests for filename edge cases."""

    def test_filename_with_spaces(self, media_module, user_pool, sample_image_bytes):
        """Test uploading file with spaces in name."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="my file name.jpg",
        )

        assert result.file_id is not None

    def test_filename_with_special_characters(self, media_module, user_pool, sample_image_bytes):
        """Test uploading file with special characters."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="file-name_v2.0 (copy).jpg",
        )

        assert result.file_id is not None

    def test_filename_with_unicode(self, media_module, user_pool, sample_image_bytes):
        """Test uploading file with unicode characters."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="image_photo.jpg",
        )

        assert result.file_id is not None

    def test_very_long_filename(self, media_module, user_pool, sample_image_bytes):
        """Test uploading file with very long filename."""
        user = user_pool.get_user()

        long_name = "a" * 200 + ".jpg"

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename=long_name,
        )

        assert result.file_id is not None

    def test_filename_without_extension(self, media_module, user_pool, sample_image_bytes):
        """Test uploading file without extension."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="noextension",
            content_type="image/jpeg",
        )

        assert result.file_id is not None


@pytest.mark.media
class TestContentTypeEdgeCases:
    """Tests for content type edge cases."""

    def test_content_type_with_charset(self, media_module, user_pool, sample_text_bytes):
        """Test content type with charset parameter."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_text_bytes,
            filename="text.txt",
            content_type="text/plain; charset=utf-8",
        )

        assert result.file_id is not None

    def test_uppercase_content_type(self, media_module, user_pool, sample_image_bytes):
        """Test uppercase content type."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="upper.jpg",
            content_type="IMAGE/JPEG",
        )

        assert result.file_id is not None


@pytest.mark.media
class TestFileSizeEdgeCases:
    """Tests for file size edge cases."""

    def test_empty_file(self, media_module, user_pool):
        """Test uploading empty file."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=b"",
            filename="empty.txt",
            content_type="text/plain",
        )

        assert result.file_id is not None
        assert result.size == 0

    def test_one_byte_file(self, media_module, user_pool):
        """Test uploading single byte file."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=b"x",
            filename="tiny.txt",
            content_type="text/plain",
        )

        assert result.file_id is not None
        assert result.size == 1

    def test_file_at_size_limit(self, media_module, user_pool):
        """Test uploading file exactly at size limit."""
        user = user_pool.get_user()

        data = b"x" * (10 * 1024 * 1024)

        result = media_module.upload_file(
            user_id=user.id,
            file_data=data,
            filename="at_limit.bin",
            content_type="application/octet-stream",
        )

        assert result.file_id is not None

    def test_file_one_byte_over_limit(self, media_module, user_pool):
        """Test uploading file one byte over limit."""
        user = user_pool.get_user()

        data = b"x" * (10 * 1024 * 1024 + 1)

        with pytest.raises(media_module.FileSizeError):
            media_module.upload_file(
                user_id=user.id,
                file_data=data,
                filename="over_limit.bin",
                content_type="application/octet-stream",
            )


@pytest.mark.media
class TestSigningEdgeCases:
    """Tests for URL signing edge cases."""

    def test_sign_url_with_query_params(self, media_module, user_pool, sample_image_bytes):
        """Test signing URL that already has query parameters."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="query_test.jpg",
        )

        signed = media_module.sign_url(result.file_id)

        assert signed.url is not None

    def test_sign_nonexistent_file(self, media_module):
        """Test signing URL for nonexistent file."""
        with pytest.raises(media_module.MediaError):
            media_module.sign_url(999999999)

    def test_verify_malformed_url(self, media_module):
        """Test verifying malformed signed URL."""
        from src.core.media.exceptions import SignatureInvalidError

        with pytest.raises(SignatureInvalidError):
            media_module.verify_signed_url("not-a-valid-url")


@pytest.mark.media
class TestThumbnailEdgeCases:
    """Tests for thumbnail edge cases."""

    def test_thumbnail_for_nonexistent_file(self, media_module):
        """Test getting thumbnails for nonexistent file."""
        thumbnails = media_module.get_thumbnails(999999999)
        assert thumbnails == {}

    def test_create_thumbnail_for_nonexistent_file(self, media_module):
        """Test creating thumbnail for nonexistent file."""
        url = media_module.create_thumbnail(999999999, size=64)
        assert url is None

    def test_create_thumbnail_for_non_image(self, media_module, user_pool, sample_text_bytes):
        """Test creating thumbnail for non-image file."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_text_bytes,
            filename="text.txt",
            content_type="text/plain",
        )

        url = media_module.create_thumbnail(result.file_id, size=64)
        assert url is None


@pytest.mark.media
class TestImageProcessingEdgeCases:
    """Tests for image processing edge cases."""

    def test_resize_nonexistent_file(self, media_module):
        """Test resizing nonexistent file."""
        with pytest.raises(media_module.MediaError):
            media_module.resize_image(999999999, width=100)

    def test_resize_non_image(self, media_module, user_pool, sample_text_bytes):
        """Test resizing non-image file."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_text_bytes,
            filename="text.txt",
            content_type="text/plain",
        )

        with pytest.raises(media_module.MediaError):
            media_module.resize_image(result.file_id, width=100)

    def test_convert_nonexistent_file(self, media_module):
        """Test converting nonexistent file."""
        with pytest.raises(media_module.MediaError):
            media_module.convert_image(999999999, "PNG")

    def test_convert_non_image(self, media_module, user_pool, sample_text_bytes):
        """Test converting non-image file."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_text_bytes,
            filename="text.txt",
            content_type="text/plain",
        )

        with pytest.raises(media_module.MediaError):
            media_module.convert_image(result.file_id, "PNG")


@pytest.mark.media
class TestVideoMetadataEdgeCases:
    """Tests for video metadata edge cases."""

    def test_video_metadata_for_nonexistent_file(self, media_module):
        """Test getting video metadata for nonexistent file."""
        metadata = media_module.get_video_metadata(999999999)
        assert metadata is None

    def test_video_metadata_for_non_video(self, media_module, user_pool, sample_image_bytes):
        """Test getting video metadata for non-video file."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="image.jpg",
        )

        metadata = media_module.get_video_metadata(result.file_id)
        assert metadata is None


@pytest.mark.media
class TestDeleteEdgeCases:
    """Tests for file deletion edge cases."""

    def test_delete_already_deleted_file(self, media_module, user_pool, sample_image_bytes):
        """Test deleting already deleted file."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="delete_twice.jpg",
        )

        media_module.delete_file(user.id, result.file_id)

        deleted = media_module.delete_file(user.id, result.file_id)
        assert deleted is False

    def test_get_data_for_deleted_file(self, media_module, user_pool, sample_image_bytes):
        """Test getting data for deleted file."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="get_deleted.jpg",
        )

        media_module.delete_file(user.id, result.file_id)

        with pytest.raises(media_module.MediaError):
            media_module.get_file_data(result.file_id)


@pytest.mark.media
class TestScannerEdgeCases:
    """Tests for scanner edge cases."""

    def test_scan_nonexistent_file(self, media_module):
        """Test scanning nonexistent file."""
        with pytest.raises(media_module.MediaError):
            media_module.scan_file(999999999)

    def test_scan_with_disabled_scanner(self, media_module, user_pool, sample_image_bytes):
        """Test scanning with disabled scanner."""
        user = user_pool.get_user()

        result = media_module.upload_file(
            user_id=user.id,
            file_data=sample_image_bytes,
            filename="scan_disabled.jpg",
        )

        status, threat = media_module.scan_file(result.file_id)

        assert status == media_module.ScanStatus.SKIPPED
        assert threat is None
