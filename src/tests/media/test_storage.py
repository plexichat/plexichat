"""
Tests for storage backends.
"""

import os
import io
import pytest


@pytest.mark.media
class TestLocalStorage:
    """Tests for local filesystem storage backend."""

    def test_store_and_retrieve(self, temp_upload_dir):
        """Test storing and retrieving a file."""
        from src.core.media.storage.local import LocalStorage
        
        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        
        data = b"test file content"
        path = "test/file.txt"
        
        storage.store(data, path, "text/plain")
        retrieved = storage.retrieve(path)
        
        assert retrieved == data

    def test_store_creates_directories(self, temp_upload_dir):
        """Test that store creates necessary directories."""
        from src.core.media.storage.local import LocalStorage
        
        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        
        path = "deep/nested/directory/file.txt"
        storage.store(b"content", path, "text/plain")
        
        full_path = os.path.join(temp_upload_dir, path)
        assert os.path.exists(full_path)

    def test_store_stream(self, temp_upload_dir):
        """Test storing from a stream."""
        from src.core.media.storage.local import LocalStorage
        
        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        
        data = b"stream content"
        stream = io.BytesIO(data)
        path = "stream/file.txt"
        
        storage.store_stream(stream, path, "text/plain", len(data))
        retrieved = storage.retrieve(path)
        
        assert retrieved == data

    def test_retrieve_stream(self, temp_upload_dir):
        """Test retrieving as a stream."""
        from src.core.media.storage.local import LocalStorage
        
        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        
        data = b"stream retrieve content"
        path = "retrieve_stream.txt"
        storage.store(data, path, "text/plain")
        
        stream, size = storage.retrieve_stream(path)
        retrieved = stream.read()
        stream.close()
        
        assert retrieved == data
        assert size == len(data)

    def test_delete_file(self, temp_upload_dir):
        """Test deleting a file."""
        from src.core.media.storage.local import LocalStorage
        
        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        
        path = "delete_me.txt"
        storage.store(b"content", path, "text/plain")
        
        assert storage.exists(path) is True
        
        result = storage.delete(path)
        
        assert result is True
        assert storage.exists(path) is False

    def test_delete_nonexistent_returns_false(self, temp_upload_dir):
        """Test deleting nonexistent file returns False."""
        from src.core.media.storage.local import LocalStorage
        
        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        
        result = storage.delete("nonexistent.txt")
        assert result is False

    def test_exists(self, temp_upload_dir):
        """Test checking file existence."""
        from src.core.media.storage.local import LocalStorage
        
        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        
        path = "exists_test.txt"
        
        assert storage.exists(path) is False
        
        storage.store(b"content", path, "text/plain")
        
        assert storage.exists(path) is True

    def test_get_url(self, temp_upload_dir):
        """Test getting URL for file."""
        from src.core.media.storage.local import LocalStorage
        
        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        
        url = storage.get_url("path/to/file.jpg")
        
        assert url == "/media/path/to/file.jpg"

    def test_get_size(self, temp_upload_dir):
        """Test getting file size."""
        from src.core.media.storage.local import LocalStorage
        
        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        
        data = b"size test content"
        path = "size_test.txt"
        storage.store(data, path, "text/plain")
        
        size = storage.get_size(path)
        
        assert size == len(data)

    def test_get_metadata(self, temp_upload_dir):
        """Test getting file metadata."""
        from src.core.media.storage.local import LocalStorage
        
        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        
        data = b"metadata test"
        path = "metadata_test.txt"
        storage.store(data, path, "text/plain")
        
        metadata = storage.get_metadata(path)
        
        assert metadata["exists"] is True
        assert metadata["size"] == len(data)
        assert "created_at" in metadata

    def test_path_traversal_prevention(self, temp_upload_dir):
        """Test that path traversal is prevented."""
        from src.core.media.storage.local import LocalStorage
        from src.core.media.exceptions import StorageError
        
        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        
        with pytest.raises(StorageError):
            storage.store(b"malicious", "../../../etc/passwd", "text/plain")

    def test_retrieve_nonexistent_raises_error(self, temp_upload_dir):
        """Test that retrieving nonexistent file raises error."""
        from src.core.media.storage.local import LocalStorage
        from src.core.media.exceptions import StorageReadError
        
        storage = LocalStorage(base_path=temp_upload_dir, base_url="/media")
        
        with pytest.raises(StorageReadError):
            storage.retrieve("nonexistent.txt")


def boto3_available():
    """Check if boto3 is available."""
    try:
        import boto3
        return True
    except ImportError:
        return False


@pytest.mark.media
@pytest.mark.s3
@pytest.mark.skipif(not boto3_available(), reason="boto3 not installed")
class TestS3Storage:
    """Tests for S3 storage backend with mocked client."""

    def test_store_calls_put_object(self, mock_s3_client, temp_upload_dir):
        """Test that store calls S3 put_object."""
        from src.core.media.storage.s3 import S3Storage
        
        storage = S3Storage(
            bucket="test-bucket",
            access_key="test-key",
            secret_key="test-secret",
        )
        
        storage.store(b"content", "test.txt", "text/plain")
        
        mock_s3_client.put_object.assert_called_once()
        call_kwargs = mock_s3_client.put_object.call_args[1]
        assert call_kwargs["Bucket"] == "test-bucket"
        assert call_kwargs["Key"] == "test.txt"
        assert call_kwargs["Body"] == b"content"
        assert call_kwargs["ContentType"] == "text/plain"

    def test_retrieve_calls_get_object(self, mock_s3_client, temp_upload_dir):
        """Test that retrieve calls S3 get_object."""
        from src.core.media.storage.s3 import S3Storage
        
        storage = S3Storage(
            bucket="test-bucket",
            access_key="test-key",
            secret_key="test-secret",
        )
        
        data = storage.retrieve("test.txt")
        
        mock_s3_client.get_object.assert_called_once()
        assert data == b"test content"

    def test_delete_calls_delete_object(self, mock_s3_client, temp_upload_dir):
        """Test that delete calls S3 delete_object."""
        from src.core.media.storage.s3 import S3Storage
        
        storage = S3Storage(
            bucket="test-bucket",
            access_key="test-key",
            secret_key="test-secret",
        )
        
        result = storage.delete("test.txt")
        
        mock_s3_client.delete_object.assert_called_once()
        assert result is True

    def test_exists_calls_head_object(self, mock_s3_client, temp_upload_dir):
        """Test that exists calls S3 head_object."""
        from src.core.media.storage.s3 import S3Storage
        
        storage = S3Storage(
            bucket="test-bucket",
            access_key="test-key",
            secret_key="test-secret",
        )
        
        result = storage.exists("test.txt")
        
        assert result is True

    def test_get_url_with_public_url(self, mock_s3_client, temp_upload_dir):
        """Test URL generation with custom public URL."""
        from src.core.media.storage.s3 import S3Storage
        
        storage = S3Storage(
            bucket="test-bucket",
            access_key="test-key",
            secret_key="test-secret",
            public_url="https://cdn.example.com",
        )
        
        url = storage.get_url("path/to/file.jpg")
        
        assert url == "https://cdn.example.com/path/to/file.jpg"

    def test_get_url_default_aws(self, mock_s3_client, temp_upload_dir):
        """Test default AWS URL generation."""
        from src.core.media.storage.s3 import S3Storage
        
        storage = S3Storage(
            bucket="test-bucket",
            access_key="test-key",
            secret_key="test-secret",
            region="us-west-2",
        )
        
        url = storage.get_url("file.jpg")
        
        assert "test-bucket" in url
        assert "us-west-2" in url
        assert "file.jpg" in url

    def test_path_prefix(self, mock_s3_client, temp_upload_dir):
        """Test that path prefix is applied."""
        from src.core.media.storage.s3 import S3Storage
        
        storage = S3Storage(
            bucket="test-bucket",
            access_key="test-key",
            secret_key="test-secret",
            path_prefix="uploads/media",
        )
        
        storage.store(b"content", "file.txt", "text/plain")
        
        call_kwargs = mock_s3_client.put_object.call_args[1]
        assert call_kwargs["Key"] == "uploads/media/file.txt"

    def test_get_size(self, mock_s3_client, temp_upload_dir):
        """Test getting file size from S3."""
        from src.core.media.storage.s3 import S3Storage
        
        storage = S3Storage(
            bucket="test-bucket",
            access_key="test-key",
            secret_key="test-secret",
        )
        
        size = storage.get_size("test.txt")
        
        assert size == 12

    def test_get_metadata(self, mock_s3_client, temp_upload_dir):
        """Test getting file metadata from S3."""
        from src.core.media.storage.s3 import S3Storage
        
        storage = S3Storage(
            bucket="test-bucket",
            access_key="test-key",
            secret_key="test-secret",
        )
        
        metadata = storage.get_metadata("test.txt")
        
        assert metadata["exists"] is True
        assert metadata["size"] == 12
        assert metadata["bucket"] == "test-bucket"
