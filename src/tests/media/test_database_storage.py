"""Tests for media database storage backend."""

import pytest

from src.core.media.models import StorageBackend, MediaType


MINI_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.media
class TestDatabaseStorage:
    """Tests for database storage backend routing and configuration."""

    def test_file_stored_in_db_record(self, media_manager, test_user):
        """Test that uploaded file has a database record."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="db_test.png",
            content_type="image/png",
        )
        file = media_manager.get_file(result.file_id)
        assert file is not None
        assert file.storage_backend == StorageBackend.LOCAL

    def test_auto_route_disabled_by_default(self, media_manager):
        """Test that auto-routing to database is disabled by default."""
        config = media_manager._config.get("auto_route_to_database", {})
        assert config.get("enabled", False) is False

    def test_should_route_to_database_disabled(self, media_manager):
        """Test _should_route_to_database returns False when disabled."""
        assert media_manager._should_route_to_database("text/plain", 100) is False

    def test_get_storage_by_backend_local(self, media_manager):
        """Test getting storage by backend name for local."""
        storage = media_manager._get_storage_by_backend("local")
        assert storage is not None

    def test_get_storage_by_backend_database(self, media_manager):
        """Test getting storage by backend name for database."""
        storage = media_manager._get_storage_by_backend("database")
        assert storage is not None  # Falls back to primary

    def test_file_record_has_checksum(self, media_manager, test_user):
        """Test that uploaded file has a checksum."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="checksum_test.png",
            content_type="image/png",
        )
        file = media_manager.get_file(result.file_id)
        assert file.checksum is not None
        assert len(file.checksum) == 64  # SHA-256 hex digest

    def test_file_record_has_upload_timestamp(self, media_manager, test_user):
        """Test that uploaded file has upload timestamp."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="timestamp_test.png",
            content_type="image/png",
        )
        file = media_manager.get_file(result.file_id)
        assert file.uploaded_at > 0

    def test_file_record_has_uploader(self, media_manager, test_user):
        """Test that uploaded file records the uploader."""
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=MINI_PNG,
            filename="uploader_test.png",
            content_type="image/png",
        )
        file = media_manager.get_file(result.file_id)
        assert file.uploaded_by == test_user.id
