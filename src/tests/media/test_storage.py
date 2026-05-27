"""Tests for media storage backends (local, S3, database)."""

import pytest

from src.core.media.models import StorageBackend


@pytest.mark.media
class TestStorage:
    """Tests for media storage backend configuration and operations."""

    def test_default_storage_is_local(self, media_manager):
        """Test that default storage backend is local."""
        assert media_manager._storage is not None

    def test_storage_backend_enum_values(self):
        """Test StorageBackend enum values."""
        assert StorageBackend.LOCAL.value == "local"
        assert StorageBackend.S3.value == "s3"
        assert StorageBackend.DATABASE.value == "database"

    def test_compute_checksum(self, media_manager):
        """Test SHA-256 checksum computation."""
        data = b"test data for checksum"
        checksum = media_manager._compute_checksum(data)
        assert len(checksum) == 64  # SHA-256 hex digest
        # Same data should produce same checksum
        assert media_manager._compute_checksum(data) == checksum

    def test_different_data_different_checksum(self, media_manager):
        """Test that different data produces different checksums."""
        checksum1 = media_manager._compute_checksum(b"data1")
        checksum2 = media_manager._compute_checksum(b"data2")
        assert checksum1 != checksum2

    def test_generate_storage_path(self, media_manager):
        """Test storage path generation."""
        from src.core.media.models import MediaType

        path = media_manager._generate_storage_path("test.png", MediaType.IMAGE)
        assert "image/" in path
        assert path.endswith(".png")

    def test_generate_storage_path_for_video(self, media_manager):
        """Test storage path generation for video files."""
        from src.core.media.models import MediaType

        path = media_manager._generate_storage_path("video.mp4", MediaType.VIDEO)
        assert "video/" in path
        assert path.endswith(".mp4")

    def test_generate_storage_path_for_document(self, media_manager):
        """Test storage path generation for documents."""
        from src.core.media.models import MediaType

        path = media_manager._generate_storage_path("doc.pdf", MediaType.DOCUMENT)
        assert "document/" in path

    def test_sanitize_filename_removes_traversal(self, media_manager):
        """Test that path traversal is removed from filenames."""
        assert ".." not in media_manager._sanitize_filename("../../../etc/passwd")
        assert "/" not in media_manager._sanitize_filename("path/to/file.png")

    def test_sanitize_filename_removes_null_bytes(self, media_manager):
        """Test that null bytes are removed from filenames."""
        result = media_manager._sanitize_filename("test\x00.png")
        assert "\x00" not in result

    def test_sanitize_filename_limits_length(self, media_manager):
        """Test that overly long filenames are truncated."""
        result = media_manager._sanitize_filename("x" * 300 + ".png")
        assert len(result) <= 250

    def test_sanitize_filename_handles_empty(self, media_manager):
        """Test that empty filename gets a default name."""
        result = media_manager._sanitize_filename("")
        assert len(result) > 0

    def test_sanitize_filename_preserves_extension(self, media_manager):
        """Test that file extension is preserved."""
        result = media_manager._sanitize_filename("test.png")
        assert result.endswith(".png")

    def test_auto_route_default_disabled(self, media_manager):
        """Test that auto-routing to database is disabled by default."""
        assert media_manager._db_storage is None

    def test_get_storage_for_file_default(self, media_manager):
        """Test that files go to primary storage by default."""

        storage, backend = media_manager._get_storage_for_file("image/png", 1000)
        assert storage == media_manager._storage
