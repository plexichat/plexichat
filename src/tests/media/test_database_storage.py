"""
Tests for database BLOB storage backend.
"""

import pytest
import io
from unittest.mock import Mock, MagicMock


class TestDatabaseStorage:
    """Tests for DatabaseStorage class."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        db = Mock()
        db.execute = Mock()
        db.fetch_one = Mock(return_value=None)
        db.convert_schema = Mock(side_effect=lambda x: x)  # Pass through
        return db

    @pytest.fixture
    def storage(self, mock_db):
        """Create a DatabaseStorage instance."""
        from src.core.media.storage.database import DatabaseStorage
        return DatabaseStorage(mock_db, base_url="/api/v1/media/blob", max_size=512 * 1024)

    def test_store_small_file(self, storage, mock_db):
        """Test storing a small file."""
        file_data = b"Hello, World!"
        path = "test/file.txt"
        content_type = "text/plain"
        
        mock_db.fetch_one.return_value = None  # File doesn't exist
        
        result = storage.store(file_data, path, content_type)
        
        assert result == path
        mock_db.execute.assert_called()  # INSERT was called

    def test_store_file_too_large(self, storage):
        """Test that storing a file over the limit raises error."""
        from src.core.media.storage.database import StorageWriteError
        
        # Create data larger than max_size (512KB)
        large_data = b"x" * (600 * 1024)
        
        with pytest.raises(StorageWriteError):
            storage.store(large_data, "test/large.bin", "application/octet-stream")

    def test_retrieve_existing_file(self, storage, mock_db):
        """Test retrieving an existing file."""
        file_data = b"Test content"
        mock_db.fetch_one.return_value = {"content": file_data}
        
        result = storage.retrieve("test/file.txt")
        
        assert result == file_data

    def test_retrieve_nonexistent_file(self, storage, mock_db):
        """Test retrieving a nonexistent file raises error."""
        from src.core.media.storage.database import StorageReadError
        
        mock_db.fetch_one.return_value = None
        
        with pytest.raises(StorageReadError):
            storage.retrieve("nonexistent/file.txt")

    def test_exists_true(self, storage, mock_db):
        """Test exists returns True for existing file."""
        mock_db.fetch_one.return_value = {"path": "test/file.txt"}
        
        assert storage.exists("test/file.txt") is True

    def test_exists_false(self, storage, mock_db):
        """Test exists returns False for nonexistent file."""
        mock_db.fetch_one.return_value = None
        
        assert storage.exists("nonexistent/file.txt") is False

    def test_delete_existing_file(self, storage, mock_db):
        """Test deleting an existing file."""
        mock_result = Mock()
        mock_result.rowcount = 1
        mock_db.execute.return_value = mock_result
        
        result = storage.delete("test/file.txt")
        
        assert result is True

    def test_get_url(self, storage):
        """Test URL generation."""
        path = "test/file.txt"
        url = storage.get_url(path)
        
        assert url.startswith("/api/v1/media/blob/")
        # URL should contain base64-encoded path
        assert len(url) > len("/api/v1/media/blob/")

    def test_get_size(self, storage, mock_db):
        """Test getting file size."""
        mock_db.fetch_one.return_value = {"size": 1024}
        
        size = storage.get_size("test/file.txt")
        
        assert size == 1024

    def test_get_metadata(self, storage, mock_db):
        """Test getting file metadata."""
        mock_db.fetch_one.return_value = {
            "path": "test/file.txt",
            "content_type": "text/plain",
            "size": 1024,
            "checksum": "abc123",
            "created_at": 1000000,
            "updated_at": 1000000,
        }
        
        metadata = storage.get_metadata("test/file.txt")
        
        assert metadata["exists"] is True
        assert metadata["content_type"] == "text/plain"
        assert metadata["size"] == 1024

    def test_store_stream(self, storage, mock_db):
        """Test storing from a stream."""
        file_data = b"Stream content"
        stream = io.BytesIO(file_data)
        
        mock_db.fetch_one.return_value = None
        
        result = storage.store_stream(stream, "test/stream.txt", "text/plain", len(file_data))
        
        assert result == "test/stream.txt"

    def test_retrieve_stream(self, storage, mock_db):
        """Test retrieving as a stream."""
        file_data = b"Test content"
        mock_db.fetch_one.return_value = {"content": file_data}
        
        stream, size = storage.retrieve_stream("test/file.txt")
        
        assert size == len(file_data)
        assert stream.read() == file_data

    def test_checksum_computation(self, storage):
        """Test checksum computation."""
        data = b"Test data for checksum"
        checksum = storage._compute_checksum(data)
        
        # SHA-256 produces 64 hex characters
        assert len(checksum) == 64
        # Same data should produce same checksum
        assert checksum == storage._compute_checksum(data)

    def test_get_by_checksum(self, storage, mock_db):
        """Test finding file by checksum."""
        mock_db.fetch_one.return_value = {"path": "test/file.txt"}
        
        path = storage.get_by_checksum("abc123")
        
        assert path == "test/file.txt"

    def test_get_total_size(self, storage, mock_db):
        """Test getting total storage size."""
        mock_db.fetch_one.return_value = {"total": 1048576}
        
        total = storage.get_total_size()
        
        assert total == 1048576

    def test_get_count(self, storage, mock_db):
        """Test getting blob count."""
        mock_db.fetch_one.return_value = {"count": 42}
        
        count = storage.get_count()
        
        assert count == 42


class TestDatabaseStorageIntegration:
    """Integration tests for DatabaseStorage with real database."""

    @pytest.fixture
    def db_and_storage(self, db_and_modules):
        """Get database and create storage."""
        from src.core.media.storage.database import DatabaseStorage
        
        db = db_and_modules[0]
        storage = DatabaseStorage(db, base_url="/api/v1/media/blob")
        return db, storage

    def test_full_lifecycle(self, db_and_storage):
        """Test full file lifecycle: store, retrieve, delete."""
        db, storage = db_and_storage
        
        # Store
        file_data = b"Integration test content"
        path = "integration/test.txt"
        storage.store(file_data, path, "text/plain")
        
        # Verify exists
        assert storage.exists(path) is True
        
        # Retrieve
        retrieved = storage.retrieve(path)
        assert retrieved == file_data
        
        # Get metadata
        metadata = storage.get_metadata(path)
        assert metadata["exists"] is True
        assert metadata["size"] == len(file_data)
        
        # Delete
        storage.delete(path)
        assert storage.exists(path) is False

    def test_update_existing_file(self, db_and_storage):
        """Test updating an existing file."""
        db, storage = db_and_storage
        
        path = "integration/update.txt"
        
        # Store initial
        storage.store(b"Initial content", path, "text/plain")
        
        # Update
        new_content = b"Updated content"
        storage.store(new_content, path, "text/plain")
        
        # Verify update
        retrieved = storage.retrieve(path)
        assert retrieved == new_content
