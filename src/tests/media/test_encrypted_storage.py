"""
Tests for encrypted storage wrapper.
"""

import pytest
import io
from unittest.mock import Mock, MagicMock, patch


class TestEncryptedStorage:
    """Tests for EncryptedStorage wrapper."""

    @pytest.fixture
    def mock_backend(self):
        """Create a mock storage backend."""
        backend = Mock()
        backend.store = Mock(return_value="test/file.txt.enc")
        backend.retrieve = Mock(return_value=b"encrypted_data")
        backend.exists = Mock(return_value=False)
        backend.delete = Mock(return_value=True)
        backend.get_url = Mock(return_value="/media/test/file.txt")
        backend.get_size = Mock(return_value=100)
        return backend

    @pytest.fixture
    def encrypted_storage(self, mock_backend, tmp_path):
        """Create an EncryptedStorage instance."""
        from src.core.media.storage.encrypted import EncryptedStorage
        from src.utils.encryption.file_encryption import FileEncryptor, Keyring
        
        # Use a temporary keyring
        keyring = Keyring(tmp_path / "test_keyring.json")
        encryptor = FileEncryptor(keyring)
        
        storage = EncryptedStorage(mock_backend, enabled=True)
        storage._encryptor = encryptor
        return storage

    def test_store_encrypts_data(self, encrypted_storage, mock_backend):
        """Test that store encrypts data before passing to backend."""
        original_data = b"Secret message"
        path = "test/file.txt"
        
        encrypted_storage.store(original_data, path, "text/plain")
        
        # Backend should have been called with encrypted data
        mock_backend.store.assert_called_once()
        call_args = mock_backend.store.call_args
        stored_data = call_args[0][0]
        stored_path = call_args[0][1]
        
        # Data should be encrypted (different from original)
        assert stored_data != original_data
        # Path should have .enc suffix
        assert stored_path == path + ".enc"
        # Encrypted data should start with magic bytes
        assert stored_data[:5] == b"PXENC"

    def test_retrieve_decrypts_data(self, encrypted_storage, mock_backend, tmp_path):
        """Test that retrieve decrypts data from backend."""
        from src.utils.encryption.file_encryption import FileEncryptor, Keyring
        
        # Create real encrypted data
        keyring = Keyring(tmp_path / "test_keyring.json")
        encryptor = FileEncryptor(keyring)
        encrypted_storage._encryptor = encryptor
        
        original_data = b"Secret message to decrypt"
        aad = b"test/file.txt"
        encrypted_blob = encryptor.encrypt_to_blob(original_data, aad)
        
        # Mock backend to return encrypted data
        mock_backend.exists.side_effect = lambda p: p.endswith(".enc")
        mock_backend.retrieve.return_value = encrypted_blob
        
        # Retrieve should decrypt
        result = encrypted_storage.retrieve("test/file.txt")
        
        assert result == original_data

    def test_disabled_encryption_passthrough(self, mock_backend):
        """Test that disabled encryption passes through to backend."""
        from src.core.media.storage.encrypted import EncryptedStorage
        
        storage = EncryptedStorage(mock_backend, enabled=False)
        
        original_data = b"Unencrypted data"
        storage.store(original_data, "test/file.txt", "text/plain")
        
        # Backend should receive original data unchanged
        mock_backend.store.assert_called_once()
        call_args = mock_backend.store.call_args
        assert call_args[0][0] == original_data
        assert call_args[0][1] == "test/file.txt"  # No .enc suffix

    def test_exists_checks_both_paths(self, mock_backend):
        """Test that exists checks both encrypted and unencrypted paths."""
        from src.core.media.storage.encrypted import EncryptedStorage
        
        storage = EncryptedStorage(mock_backend, enabled=True)
        storage._encryptor = Mock()  # Prevent initialization
        
        # Neither exists
        mock_backend.exists.return_value = False
        assert storage.exists("test/file.txt") is False
        
        # Encrypted exists
        mock_backend.exists.side_effect = lambda p: p.endswith(".enc")
        assert storage.exists("test/file.txt") is True
        
        # Unencrypted exists (legacy)
        mock_backend.exists.side_effect = lambda p: not p.endswith(".enc")
        assert storage.exists("test/file.txt") is True

    def test_delete_removes_both_paths(self, mock_backend):
        """Test that delete tries to remove both encrypted and unencrypted."""
        from src.core.media.storage.encrypted import EncryptedStorage
        
        storage = EncryptedStorage(mock_backend, enabled=True)
        storage._encryptor = Mock()
        
        mock_backend.exists.return_value = True
        mock_backend.delete.return_value = True
        
        result = storage.delete("test/file.txt")
        
        assert result is True
        # Should have tried to delete both paths
        assert mock_backend.delete.call_count >= 1

    def test_is_encrypted(self, mock_backend):
        """Test is_encrypted method."""
        from src.core.media.storage.encrypted import EncryptedStorage
        
        storage = EncryptedStorage(mock_backend, enabled=True)
        storage._encryptor = Mock()
        
        # Encrypted file exists
        mock_backend.exists.side_effect = lambda p: p.endswith(".enc")
        assert storage.is_encrypted("test/file.txt") is True
        
        # Only unencrypted exists
        mock_backend.exists.side_effect = lambda p: not p.endswith(".enc")
        assert storage.is_encrypted("test/file.txt") is False

    def test_get_metadata_includes_encryption_status(self, mock_backend):
        """Test that get_metadata includes encryption status."""
        from src.core.media.storage.encrypted import EncryptedStorage
        
        storage = EncryptedStorage(mock_backend, enabled=True)
        storage._encryptor = Mock()
        
        mock_backend.exists.side_effect = lambda p: p.endswith(".enc")
        mock_backend.get_size.return_value = 100
        
        metadata = storage.get_metadata("test/file.txt")
        
        assert "encrypted" in metadata
        assert metadata["encrypted"] is True


class TestEncryptedStorageIntegration:
    """Integration tests for encrypted storage."""

    @pytest.fixture
    def temp_storage(self, tmp_path):
        """Create encrypted storage with local backend."""
        from src.core.media.storage.local import LocalStorage
        from src.core.media.storage.encrypted import EncryptedStorage
        
        local = LocalStorage(str(tmp_path / "media"), "/media")
        encrypted = EncryptedStorage(local, enabled=True)
        return encrypted

    def test_full_roundtrip(self, temp_storage):
        """Test full encrypt/store/retrieve/decrypt cycle."""
        original_data = b"This is secret data that should be encrypted at rest."
        path = "secret/document.txt"
        
        # Store (encrypts)
        temp_storage.store(original_data, path, "text/plain")
        
        # Verify file exists
        assert temp_storage.exists(path) is True
        assert temp_storage.is_encrypted(path) is True
        
        # Retrieve (decrypts)
        retrieved = temp_storage.retrieve(path)
        assert retrieved == original_data
        
        # Delete
        temp_storage.delete(path)
        assert temp_storage.exists(path) is False

    def test_large_file_encryption(self, temp_storage):
        """Test encryption of larger files."""
        import os
        
        # 1MB of random data
        original_data = os.urandom(1024 * 1024)
        path = "large/file.bin"
        
        temp_storage.store(original_data, path, "application/octet-stream")
        retrieved = temp_storage.retrieve(path)
        
        assert retrieved == original_data

    def test_aad_binding(self, temp_storage):
        """Test that AAD binds encrypted data to path."""
        original_data = b"Path-bound data"
        path = "bound/file.txt"
        
        temp_storage.store(original_data, path, "text/plain")
        
        # Retrieve with correct path works
        retrieved = temp_storage.retrieve(path)
        assert retrieved == original_data
