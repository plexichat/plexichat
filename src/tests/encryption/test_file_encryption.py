"""
Tests for file encryption at rest.
"""

import os
import io
import pytest


class TestFileEncryptor:
    """Tests for FileEncryptor class."""

    @pytest.fixture
    def temp_keyring(self, tmp_path):
        """Create a temporary keyring for testing."""
        keyring_path = tmp_path / "test_keyring.json"
        return keyring_path

    @pytest.fixture
    def encryptor(self, temp_keyring):
        """Create a FileEncryptor with temporary keyring."""
        from src.utils.encryption.file_encryption import FileEncryptor, Keyring

        keyring = Keyring(temp_keyring)
        return FileEncryptor(keyring)

    def test_encrypt_decrypt_roundtrip(self, encryptor):
        """Test basic encrypt/decrypt roundtrip."""
        original_data = b"Hello, World! This is test data for encryption."

        result = encryptor.encrypt(original_data)

        assert result.encrypted_data != original_data
        assert result.header.version == 2
        assert result.header.original_size == len(original_data)

        decrypted = encryptor.decrypt(result.encrypted_data, result.header)

        assert decrypted.data == original_data
        assert decrypted.verified is True

    def test_encrypt_with_aad(self, encryptor):
        """Test encryption with Additional Authenticated Data."""
        original_data = b"Secret message"
        aad = b"file_id:12345"

        result = encryptor.encrypt(original_data, aad)

        # Decrypt with correct AAD
        decrypted = encryptor.decrypt(result.encrypted_data, result.header, aad)
        assert decrypted.data == original_data

        # Decrypt with wrong AAD should fail
        with pytest.raises(Exception):
            encryptor.decrypt(result.encrypted_data, result.header, b"wrong_aad")

    def test_encrypt_to_blob(self, encryptor):
        """Test encrypt_to_blob and decrypt_from_blob."""
        original_data = b"Test data for blob encryption"

        blob = encryptor.encrypt_to_blob(original_data)

        # Blob should start with magic bytes
        assert blob[:5] == b"PXSTR"

        decrypted = encryptor.decrypt_from_blob(blob)
        assert decrypted == original_data

    def test_encrypt_empty_data_fails(self, encryptor):
        """Test that encrypting empty data raises error."""
        with pytest.raises(ValueError, match="Cannot encrypt empty data"):
            encryptor.encrypt(b"")

    def test_header_serialization(self, encryptor):
        """Test header serialization and deserialization."""
        original_data = b"Test data"
        result = encryptor.encrypt(original_data)

        # Serialize header
        header_bytes = encryptor.serialize_header(result.header)

        # Deserialize header
        parsed_header, header_size = encryptor.deserialize_header(header_bytes)

        assert parsed_header.version == result.header.version
        assert parsed_header.key_version == result.header.key_version
        assert parsed_header.original_size == result.header.original_size
        assert parsed_header.checksum == result.header.checksum

    def test_checksum_verification(self, encryptor):
        """Test that checksum verification catches corruption."""
        original_data = b"Test data for checksum"
        result = encryptor.encrypt(original_data)

        # Corrupt the header checksum
        result.header.checksum = "0" * 64

        decrypted = encryptor.decrypt(
            result.encrypted_data, result.header, verify_checksum=True
        )

        assert decrypted.verified is False

    def test_large_file_encryption(self, encryptor):
        """Test encryption of larger files."""
        # 1MB of random data
        original_data = os.urandom(1024 * 1024)

        blob = encryptor.encrypt_to_blob(original_data)
        decrypted = encryptor.decrypt_from_blob(blob)

        assert decrypted == original_data


class TestStreamingFileEncryptor:
    """Tests for StreamingFileEncryptor class."""

    @pytest.fixture
    def temp_keyring(self, tmp_path):
        """Create a temporary keyring for testing."""
        keyring_path = tmp_path / "test_keyring.json"
        return keyring_path

    @pytest.fixture
    def streaming_encryptor(self, temp_keyring):
        """Create a StreamingFileEncryptor with temporary keyring."""
        from src.utils.encryption.file_encryption import StreamingFileEncryptor, Keyring

        keyring = Keyring(temp_keyring)
        return StreamingFileEncryptor(keyring, chunk_size=1024)

    def test_stream_encrypt_decrypt_roundtrip(self, streaming_encryptor):
        """Test streaming encrypt/decrypt roundtrip."""
        original_data = os.urandom(10 * 1024)  # 10KB

        input_stream = io.BytesIO(original_data)
        encrypted_stream = io.BytesIO()

        metadata = streaming_encryptor.encrypt_stream(
            input_stream, encrypted_stream, len(original_data)
        )

        assert metadata["chunks"] > 1
        assert metadata["checksum"] is not None

        # Decrypt
        encrypted_stream.seek(0)
        decrypted_stream = io.BytesIO()

        decrypt_metadata = streaming_encryptor.decrypt_stream(
            encrypted_stream, decrypted_stream
        )

        decrypted_stream.seek(0)
        decrypted_data = decrypted_stream.read()

        assert decrypted_data == original_data
        assert decrypt_metadata["verified"] is True

    def test_stream_with_aad(self, streaming_encryptor):
        """Test streaming encryption with AAD."""
        original_data = b"Stream test data" * 100
        aad = b"stream_file_id:67890"

        input_stream = io.BytesIO(original_data)
        encrypted_stream = io.BytesIO()

        streaming_encryptor.encrypt_stream(
            input_stream, encrypted_stream, len(original_data), aad
        )

        encrypted_stream.seek(0)
        decrypted_stream = io.BytesIO()

        metadata = streaming_encryptor.decrypt_stream(
            encrypted_stream, decrypted_stream, aad
        )

        decrypted_stream.seek(0)
        assert decrypted_stream.read() == original_data
        assert metadata["verified"] is True


class TestModuleFunctions:
    """Tests for module-level convenience functions."""

    def test_encrypt_file_function(self, tmp_path):
        """Test encrypt_file convenience function."""
        # Use a temporary keyring
        import src.utils.encryption.file_encryption as fe
        from src.utils.encryption.file_encryption import Keyring, FileEncryptor

        keyring = Keyring(tmp_path / "keyring.json")
        fe._file_encryptor = FileEncryptor(keyring)

        original_data = b"Test data for module function"

        encrypted = fe.encrypt_file(original_data)
        decrypted = fe.decrypt_file(encrypted)

        assert decrypted == original_data
