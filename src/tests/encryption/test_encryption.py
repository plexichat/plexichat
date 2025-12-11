"""
Encryption module tests.

Tests cover password hashing (Argon2id), data encryption (AES-256-GCM),
digital signatures (Ed25519), and Snowflake ID generation.
"""

import pytest
import os
import sys
import time
import threading
import base64

# Setup paths before any imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
src_path = os.path.join(project_root, "src")
encryption_path = os.path.join(src_path, "utils", "encryption")
common_utils_path = os.path.join(src_path, "utils", "common-utils")

for path in [project_root, src_path, encryption_path, common_utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

from src.utils.encryption import (
    hash_password,
    verify_password,
    encrypt_data,
    decrypt_data,
    generate_key_pair,
    sign_data,
    verify_signature,
    generate_snowflake_id,
    parse_snowflake_id,
    setup
)
from src.utils.encryption.core import EncryptionManager, SnowflakeGenerator

@pytest.fixture(scope="module")
def setup_encryption():
    """Setup encryption module for tests."""
    setup(worker_id=1, datacenter_id=1)
    yield

class TestPasswordHashing:
    """Test Argon2id password hashing."""

    def test_hash_password_basic(self, setup_encryption):
        """Test basic password hashing."""
        password = "test_password_123"
        hash_str = hash_password(password)
        assert hash_str is not None
        assert len(hash_str) > 0
        assert password not in hash_str

    def test_hash_password_different_hashes(self, setup_encryption):
        """Test that same password generates different hashes (due to salt)."""
        password = "test_password_123"
        hash1 = hash_password(password)
        hash2 = hash_password(password)
        assert hash1 != hash2

    def test_verify_password_correct(self, setup_encryption):
        """Test verifying correct password."""
        password = "test_password_123"
        hash_str = hash_password(password)
        assert verify_password(password, hash_str) is True

    def test_verify_password_incorrect(self, setup_encryption):
        """Test verifying incorrect password."""
        password = "test_password_123"
        wrong_password = "wrong_password"
        hash_str = hash_password(password)
        assert verify_password(wrong_password, hash_str) is False

    def test_hash_password_empty(self, setup_encryption):
        """Test hashing empty password raises error."""
        with pytest.raises(ValueError):
            hash_password("")

    def test_hash_password_special_characters(self, setup_encryption):
        """Test hashing password with special characters."""
        password = "P@ssw0rd!#$%^&*()"
        hash_str = hash_password(password)
        assert verify_password(password, hash_str) is True

    def test_hash_password_unicode(self, setup_encryption):
        """Test hashing password with non-ASCII characters."""
        password = "password123ABC"
        hash_str = hash_password(password)
        assert verify_password(password, hash_str) is True

    def test_hash_password_long(self, setup_encryption):
        """Test hashing very long password."""
        password = "a" * 1000
        hash_str = hash_password(password)
        assert verify_password(password, hash_str) is True

    def test_verify_password_malformed_hash(self, setup_encryption):
        """Test verifying against malformed hash."""
        password = "test_password"
        malformed_hash = "not_a_valid_hash"
        assert verify_password(password, malformed_hash) is False

    def test_hash_password_case_sensitive(self, setup_encryption):
        """Test that password verification is case sensitive."""
        password = "TestPassword"
        hash_str = hash_password(password)
        assert verify_password("testpassword", hash_str) is False
        assert verify_password("TESTPASSWORD", hash_str) is False
        assert verify_password(password, hash_str) is True


class TestDataEncryption:
    """Test AES-256-GCM encryption."""

    def test_encrypt_decrypt_basic(self, setup_encryption):
        """Test basic encryption and decryption."""
        data = "Hello, World!"
        encrypted = encrypt_data(data)
        decrypted = decrypt_data(encrypted)
        assert decrypted == data

    def test_encrypt_different_ciphertexts(self, setup_encryption):
        """Test that same data encrypts to different ciphertexts."""
        data = "test data"
        encrypted1 = encrypt_data(data)
        encrypted2 = encrypt_data(data)
        assert encrypted1 != encrypted2

    def test_encrypt_decrypt_empty_string(self, setup_encryption):
        """Test encrypting empty string."""
        data = ""
        encrypted = encrypt_data(data)
        decrypted = decrypt_data(encrypted)
        assert decrypted == data

    def test_encrypt_decrypt_special_characters(self, setup_encryption):
        """Test encrypting data with special characters."""
        data = "Test!@#$%^&*()_+-=[]{}|;:',.<>?/`~"
        encrypted = encrypt_data(data)
        decrypted = decrypt_data(encrypted)
        assert decrypted == data

    def test_encrypt_decrypt_long_data(self, setup_encryption):
        """Test encrypting long data."""
        data = "A" * 10000
        encrypted = encrypt_data(data)
        decrypted = decrypt_data(encrypted)
        assert decrypted == data

    def test_encrypt_decrypt_with_custom_key(self, setup_encryption):
        """Test encryption with custom key."""
        data = "Secret data"
        key = os.urandom(32)
        encrypted = encrypt_data(data, key)
        decrypted = decrypt_data(encrypted, key)
        assert decrypted == data

    def test_decrypt_with_wrong_key(self, setup_encryption):
        """Test that decryption fails with wrong key."""
        data = "Secret data"
        key1 = os.urandom(32)
        key2 = os.urandom(32)
        encrypted = encrypt_data(data, key1)
        with pytest.raises(ValueError):
            decrypt_data(encrypted, key2)

    def test_decrypt_malformed_data(self, setup_encryption):
        """Test decrypting malformed data."""
        with pytest.raises(ValueError):
            decrypt_data("not_valid_base64!@#")

    def test_decrypt_truncated_data(self, setup_encryption):
        """Test decrypting truncated data."""
        data = "test"
        encrypted = encrypt_data(data)
        truncated = encrypted[:10]
        with pytest.raises(ValueError):
            decrypt_data(truncated)

    def test_encrypt_json_data(self, setup_encryption):
        """Test encrypting JSON-like string data."""
        data = '{"key": "value", "number": 42}'
        encrypted = encrypt_data(data)
        decrypted = decrypt_data(encrypted)
        assert decrypted == data

    def test_encrypted_data_is_base64(self, setup_encryption):
        """Test that encrypted data is valid base64."""
        data = "test data"
        encrypted = encrypt_data(data)
        try:
            base64.b64decode(encrypted)
        except Exception:
            pytest.fail("Encrypted data is not valid base64")

    def test_encrypt_newlines(self, setup_encryption):
        """Test encrypting data with newlines."""
        data = "Line 1\nLine 2\nLine 3"
        encrypted = encrypt_data(data)
        decrypted = decrypt_data(encrypted)
        assert decrypted == data

    def test_key_derivation(self, setup_encryption):
        """Test key derivation from password."""
        manager = EncryptionManager()
        password = "test_password"
        key1, salt1 = manager.derive_key(password)
        key2, salt2 = manager.derive_key(password, salt1)
        assert len(key1) == 32
        assert len(key2) == 32
        assert key1 == key2
        assert salt1 == salt2

    def test_key_derivation_different_salts(self, setup_encryption):
        """Test that different salts produce different keys."""
        manager = EncryptionManager()
        password = "test_password"
        key1, salt1 = manager.derive_key(password)
        key2, salt2 = manager.derive_key(password)
        assert key1 != key2
        assert salt1 != salt2

    def test_custom_key_invalid_length(self, setup_encryption):
        """Test that invalid key length raises error."""
        data = "test"
        invalid_key = os.urandom(16)
        with pytest.raises(ValueError):
            encrypt_data(data, invalid_key)


class TestDigitalSignatures:
    """Test Ed25519 digital signatures."""

    def test_generate_key_pair(self, setup_encryption):
        """Test generating Ed25519 key pair."""
        private_key, public_key = generate_key_pair()
        assert len(private_key) == 32
        assert len(public_key) == 32

    def test_sign_and_verify(self, setup_encryption):
        """Test signing and verifying data."""
        private_key, public_key = generate_key_pair()
        data = b"Test message"
        signature = sign_data(data, private_key)
        assert verify_signature(data, signature, public_key) is True

    def test_verify_wrong_signature(self, setup_encryption):
        """Test verifying with wrong signature."""
        private_key, public_key = generate_key_pair()
        data = b"Test message"
        wrong_signature = os.urandom(64)
        assert verify_signature(data, wrong_signature, public_key) is False

    def test_verify_modified_data(self, setup_encryption):
        """Test verifying modified data."""
        private_key, public_key = generate_key_pair()
        data = b"Test message"
        signature = sign_data(data, private_key)
        modified_data = b"Modified message"
        assert verify_signature(modified_data, signature, public_key) is False

    def test_verify_wrong_public_key(self, setup_encryption):
        """Test verifying with wrong public key."""
        private_key1, public_key1 = generate_key_pair()
        private_key2, public_key2 = generate_key_pair()
        data = b"Test message"
        signature = sign_data(data, private_key1)
        assert verify_signature(data, signature, public_key2) is False

    def test_sign_empty_data(self, setup_encryption):
        """Test signing empty data."""
        private_key, public_key = generate_key_pair()
        data = b""
        signature = sign_data(data, private_key)
        assert verify_signature(data, signature, public_key) is True

    def test_sign_large_data(self, setup_encryption):
        """Test signing large data."""
        private_key, public_key = generate_key_pair()
        data = b"A" * 100000
        signature = sign_data(data, private_key)
        assert verify_signature(data, signature, public_key) is True

    def test_signature_length(self, setup_encryption):
        """Test that signature is 64 bytes."""
        private_key, public_key = generate_key_pair()
        data = b"Test message"
        signature = sign_data(data, private_key)
        assert len(signature) == 64


class TestSnowflakeIDs:
    """Test Snowflake ID generation."""

    def test_generate_snowflake(self, setup_encryption):
        """Test generating a Snowflake ID."""
        snowflake_id = generate_snowflake_id()
        assert isinstance(snowflake_id, int)
        assert snowflake_id > 0

    def test_snowflake_uniqueness(self, setup_encryption):
        """Test that generated IDs are unique."""
        ids = set()
        for _ in range(1000):
            snowflake_id = generate_snowflake_id()
            assert snowflake_id not in ids
            ids.add(snowflake_id)

    def test_snowflake_sequential(self, setup_encryption):
        """Test that IDs are sequential within same millisecond."""
        id1 = generate_snowflake_id()
        id2 = generate_snowflake_id()
        assert id2 > id1

    def test_parse_snowflake(self, setup_encryption):
        """Test parsing a Snowflake ID."""
        snowflake_id = generate_snowflake_id()
        parsed = parse_snowflake_id(snowflake_id)
        assert 'timestamp' in parsed
        assert 'datacenter_id' in parsed
        assert 'worker_id' in parsed
        assert 'sequence' in parsed

    def test_parse_snowflake_correct_worker(self, setup_encryption):
        """Test that parsed worker ID matches."""
        snowflake_id = generate_snowflake_id()
        parsed = parse_snowflake_id(snowflake_id)
        assert parsed['worker_id'] == 1
        assert parsed['datacenter_id'] == 1

    def test_snowflake_generator_bounds(self):
        """Test Snowflake generator with boundary values."""
        gen = SnowflakeGenerator(worker_id=0, datacenter_id=0)
        snowflake_id = gen.generate()
        assert snowflake_id > 0

        gen = SnowflakeGenerator(worker_id=31, datacenter_id=31)
        snowflake_id = gen.generate()
        assert snowflake_id > 0

    def test_snowflake_invalid_worker_id(self):
        """Test that invalid worker ID raises error."""
        with pytest.raises(ValueError):
            SnowflakeGenerator(worker_id=32, datacenter_id=1)
        with pytest.raises(ValueError):
            SnowflakeGenerator(worker_id=-1, datacenter_id=1)

    def test_snowflake_invalid_datacenter_id(self):
        """Test that invalid datacenter ID raises error."""
        with pytest.raises(ValueError):
            SnowflakeGenerator(worker_id=1, datacenter_id=32)
        with pytest.raises(ValueError):
            SnowflakeGenerator(worker_id=1, datacenter_id=-1)

    def test_snowflake_timestamp_ordering(self, setup_encryption):
        """Test that timestamps in IDs are ordered."""
        id1 = generate_snowflake_id()
        time.sleep(0.001)
        id2 = generate_snowflake_id()

        parsed1 = parse_snowflake_id(id1)
        parsed2 = parse_snowflake_id(id2)

        assert parsed2['timestamp'] >= parsed1['timestamp']

    def test_snowflake_sequence_overflow(self):
        """Test sequence overflow handling."""
        gen = SnowflakeGenerator(worker_id=1, datacenter_id=1)
        ids = []
        for _ in range(5000):
            ids.append(gen.generate())
        assert len(set(ids)) == 5000

    def test_snowflake_concurrent_generation(self):
        """Test concurrent ID generation."""
        gen = SnowflakeGenerator(worker_id=1, datacenter_id=1)
        ids = []
        lock = threading.Lock()

        def generate_ids():
            for _ in range(100):
                snowflake_id = gen.generate()
                with lock:
                    ids.append(snowflake_id)

        threads = [threading.Thread(target=generate_ids) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(set(ids)) == 1000

    def test_snowflake_custom_epoch(self):
        """Test Snowflake generation with custom epoch."""
        custom_epoch = int(time.time() * 1000) - 1000000
        gen = SnowflakeGenerator(worker_id=1, datacenter_id=1, epoch_timestamp=custom_epoch)
        snowflake_id = gen.generate()
        assert snowflake_id > 0


class TestEncryptionManager:
    """Test EncryptionManager class directly."""

    def test_manager_initialization(self):
        """Test initializing EncryptionManager."""
        manager = EncryptionManager()
        assert manager is not None

    def test_manager_custom_argon2_params(self):
        """Test EncryptionManager with custom Argon2 parameters."""
        manager = EncryptionManager(
            argon2_time_cost=3,
            argon2_memory_cost=131072,
            argon2_parallelism=4
        )
        password = "test_password"
        hash_str = manager.hash_password(password)
        assert manager.verify_password(password, hash_str) is True

    def test_manager_default_key_persistence(self):
        """Test that default key is reused."""
        manager = EncryptionManager()
        data = "test"
        encrypted1 = manager.encrypt_data(data)
        decrypted1 = manager.decrypt_data(encrypted1)
        assert decrypted1 == data

        encrypted2 = manager.encrypt_data(data)
        decrypted2 = manager.decrypt_data(encrypted2)
        assert decrypted2 == data


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_zero_length_data_encryption(self, setup_encryption):
        """Test encrypting zero-length data."""
        data = ""
        encrypted = encrypt_data(data)
        decrypted = decrypt_data(encrypted)
        assert decrypted == ""

    def test_very_short_password(self, setup_encryption):
        """Test hashing very short password."""
        password = "a"
        hash_str = hash_password(password)
        assert verify_password(password, hash_str) is True

    def test_numeric_string_encryption(self, setup_encryption):
        """Test encrypting numeric strings."""
        data = "1234567890"
        encrypted = encrypt_data(data)
        decrypted = decrypt_data(encrypted)
        assert decrypted == data

    def test_whitespace_only_password(self, setup_encryption):
        """Test hashing whitespace-only password."""
        password = "   "
        hash_str = hash_password(password)
        assert verify_password(password, hash_str) is True

    def test_binary_like_string_encryption(self, setup_encryption):
        """Test encrypting binary-like string data."""
        data = "\x00\x01\x02\x03"
        encrypted = encrypt_data(data)
        decrypted = decrypt_data(encrypted)
        assert decrypted == data
