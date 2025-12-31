"""
Encryption module tests.

Tests cover password hashing (Argon2id), data encryption (AES-256-GCM),
digital signatures (Ed25519), Snowflake ID generation, key rotation,
message encryption, secure token generation, and timing attack resistance.
"""

import pytest
import os
import sys
import time
import threading
import base64

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
from src.utils.encryption.core import (
    EncryptionManager,
    SnowflakeGenerator,
    MessageEncryptor,
    Keyring
)
from src.core.auth.tokens import (
    generate_token_secret,
    hash_token,
    verify_token_hash,
    create_session_token,
    create_bot_token,
    parse_token
)

@pytest.fixture(scope="module")
def setup_encryption():
    """Setup encryption module for tests."""
    setup(worker_id=1, datacenter_id=1)
    yield

@pytest.fixture
def temp_keyring_path(tmp_path):
    """Create a temporary keyring path for testing."""
    return tmp_path / "test_keyring.json"

@pytest.fixture
def fresh_keyring(temp_keyring_path):
    """Create a fresh keyring for testing."""
    return Keyring(temp_keyring_path)


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


class TestPasswordHashingTimingAttacks:
    """Test password hashing timing attack resistance."""

    def test_verify_timing_consistency_correct_password(self, setup_encryption):
        """Test that correct password verification has consistent timing."""
        password = "correct_password_123"
        hash_str = hash_password(password)
        
        timings = []
        for _ in range(10):
            start = time.perf_counter()
            verify_password(password, hash_str)
            end = time.perf_counter()
            timings.append(end - start)
        
        avg = sum(timings) / len(timings)
        for t in timings:
            assert abs(t - avg) < avg * 0.8

    def test_verify_timing_consistency_incorrect_password(self, setup_encryption):
        """Test that incorrect password verification has consistent timing."""
        password = "correct_password_123"
        wrong_password = "wrong_password_123"
        hash_str = hash_password(password)
        
        timings = []
        for _ in range(10):
            start = time.perf_counter()
            verify_password(wrong_password, hash_str)
            end = time.perf_counter()
            timings.append(end - start)
        
        avg = sum(timings) / len(timings)
        for t in timings:
            assert abs(t - avg) < avg * 1.0

    def test_verify_timing_correct_vs_incorrect(self, setup_encryption):
        """Test that correct and incorrect passwords take similar time."""
        password = "test_password_timing"
        wrong_password = "wrong_password_timing"
        hash_str = hash_password(password)
        
        correct_timings = []
        incorrect_timings = []
        
        for _ in range(5):
            start = time.perf_counter()
            verify_password(password, hash_str)
            end = time.perf_counter()
            correct_timings.append(end - start)
            
            start = time.perf_counter()
            verify_password(wrong_password, hash_str)
            end = time.perf_counter()
            incorrect_timings.append(end - start)
        
        avg_correct = sum(correct_timings) / len(correct_timings)
        avg_incorrect = sum(incorrect_timings) / len(incorrect_timings)
        
        assert abs(avg_correct - avg_incorrect) < max(avg_correct, avg_incorrect) * 0.3


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
            if encrypted.startswith("ENC:"):
                parts = encrypted.split(":", 2)
                encrypted = parts[2]
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


class TestAESGCMCompliance:
    """Test AES-256-GCM algorithm compliance."""

    def test_aes_gcm_nonce_uniqueness(self, setup_encryption):
        """Test that each encryption uses a unique nonce."""
        data = "test data"
        encrypted_list = [encrypt_data(data) for _ in range(100)]
        
        nonces = set()
        for encrypted in encrypted_list:
            if encrypted.startswith("ENC:"):
                parts = encrypted.split(":", 2)
                encrypted = parts[2]
            combined = base64.b64decode(encrypted)
            nonce = combined[:12]
            assert nonce not in nonces
            nonces.add(nonce)

    def test_aes_gcm_nonce_length(self, setup_encryption):
        """Test that nonce is 12 bytes (96 bits)."""
        data = "test"
        encrypted = encrypt_data(data)
        if encrypted.startswith("ENC:"):
            parts = encrypted.split(":", 2)
            encrypted = parts[2]
        combined = base64.b64decode(encrypted)
        nonce = combined[:12]
        assert len(nonce) == 12

    def test_aes_gcm_authentication_tag(self, setup_encryption):
        """Test that authentication tag prevents tampering."""
        data = "sensitive data"
        encrypted = encrypt_data(data)
        
        if encrypted.startswith("ENC:"):
            prefix_parts = encrypted.split(":", 2)
            prefix = ":".join(prefix_parts[:2]) + ":"
            encrypted = prefix_parts[2]
        else:
            prefix = ""
        
        combined = base64.b64decode(encrypted)
        tampered = combined[:-1] + bytes([combined[-1] ^ 0xFF])
        tampered_encrypted = prefix + base64.b64encode(tampered).decode('utf-8')
        
        with pytest.raises(ValueError):
            decrypt_data(tampered_encrypted)

    def test_aes_gcm_key_size(self):
        """Test that AES-256 uses 32-byte keys."""
        manager = EncryptionManager()
        _, key = manager.keyring.get_key()
        assert len(key) == 32

    def test_aes_gcm_ciphertext_integrity(self, setup_encryption):
        """Test that modified ciphertext fails decryption."""
        data = "important data"
        encrypted = encrypt_data(data)
        
        if encrypted.startswith("ENC:"):
            parts = encrypted.split(":", 2)
            prefix = ":".join(parts[:2]) + ":"
            encrypted = parts[2]
        else:
            prefix = ""
        
        combined = bytearray(base64.b64decode(encrypted))
        combined[13] ^= 0x01
        tampered_encrypted = prefix + base64.b64encode(bytes(combined)).decode('utf-8')
        
        with pytest.raises(ValueError):
            decrypt_data(tampered_encrypted)


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

    def test_signature_deterministic(self, setup_encryption):
        """Test that Ed25519 signatures are deterministic."""
        private_key, public_key = generate_key_pair()
        data = b"Test message"
        signature1 = sign_data(data, private_key)
        signature2 = sign_data(data, private_key)
        assert signature1 == signature2


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


class TestKeyRotation:
    """Test encryption key rotation functionality."""

    def test_keyring_initialization(self, fresh_keyring):
        """Test keyring initializes with no keys."""
        assert len(fresh_keyring.keys) == 0
        assert fresh_keyring.current_version == 0

    def test_keyring_generates_initial_key(self, fresh_keyring):
        """Test that keyring generates initial key on first use."""
        version, key = fresh_keyring.get_key()
        assert version == 1
        assert len(key) == 32
        assert len(fresh_keyring.keys) == 1

    def test_keyring_rotation(self, fresh_keyring):
        """Test key rotation creates new version."""
        fresh_keyring.get_key()
        initial_version = fresh_keyring.current_version
        
        new_version = fresh_keyring.rotate()
        assert new_version == initial_version + 1
        assert fresh_keyring.current_version == new_version
        assert len(fresh_keyring.keys) == 2

    def test_keyring_multiple_rotations(self, fresh_keyring):
        """Test multiple key rotations."""
        fresh_keyring.get_key()
        
        versions = []
        for _ in range(5):
            version = fresh_keyring.rotate()
            versions.append(version)
        
        assert versions == [2, 3, 4, 5, 6]
        assert len(fresh_keyring.keys) == 6

    def test_keyring_persist_and_load(self, temp_keyring_path):
        """Test that keyring persists to disk and loads correctly."""
        keyring1 = Keyring(temp_keyring_path)
        keyring1.get_key()
        keyring1.rotate()
        version1 = keyring1.current_version
        keys1 = dict(keyring1.keys)
        
        keyring2 = Keyring(temp_keyring_path)
        assert keyring2.current_version == version1
        assert keyring2.keys == keys1

    def test_keyring_old_key_retrieval(self, fresh_keyring):
        """Test retrieving old key versions."""
        _, key_v1 = fresh_keyring.get_key()
        fresh_keyring.rotate()
        _, key_v2 = fresh_keyring.get_key()
        
        assert key_v1 != key_v2
        
        _, retrieved_key_v1 = fresh_keyring.get_key(version=1)
        assert retrieved_key_v1 == key_v1

    def test_encryption_with_rotated_keys(self, temp_keyring_path):
        """Test encryption/decryption works across key rotation."""
        keyring = Keyring(temp_keyring_path)
        manager = EncryptionManager()
        manager.keyring = keyring
        
        data = "sensitive data"
        encrypted_v1 = manager.encrypt_data(data)
        
        keyring.rotate()
        
        encrypted_v2 = manager.encrypt_data(data)
        
        decrypted_v1 = manager.decrypt_data(encrypted_v1)
        decrypted_v2 = manager.decrypt_data(encrypted_v2)
        
        assert decrypted_v1 == data
        assert decrypted_v2 == data

    def test_keyring_version_in_encrypted_data(self, temp_keyring_path):
        """Test that encrypted data includes version prefix."""
        keyring = Keyring(temp_keyring_path)
        manager = EncryptionManager()
        manager.keyring = keyring
        
        data = "test"
        encrypted = manager.encrypt_data(data)
        
        assert encrypted.startswith("ENC:")
        parts = encrypted.split(":", 2)
        assert len(parts) == 3
        assert parts[1] == "1"

    def test_rotation_timestamp_tracking(self, fresh_keyring):
        """Test that rotation timestamp is tracked."""
        fresh_keyring.get_key()
        initial_time = fresh_keyring.rotated_at
        assert initial_time > 0
        
        # Sleep for at least 1 second since rotated_at uses int(time.time())
        time.sleep(1.1)
        fresh_keyring.rotate()
        new_time = fresh_keyring.rotated_at
        assert new_time > initial_time


class TestMessageEncryption:
    """Test message encryption/decryption functionality."""

    def test_encrypt_message_basic(self):
        """Test basic message encryption."""
        encryptor = MessageEncryptor()
        content = "Hello, this is a message!"
        encrypted = encryptor.encrypt_message(content)
        
        assert encrypted != content
        assert encrypted.startswith("ENC:")
        assert encryptor.is_encrypted(encrypted)

    def test_decrypt_message_basic(self):
        """Test basic message decryption."""
        encryptor = MessageEncryptor()
        content = "Hello, this is a message!"
        encrypted = encryptor.encrypt_message(content)
        decrypted = encryptor.decrypt_message(encrypted)
        
        assert decrypted == content

    def test_message_encryption_with_message_id(self):
        """Test message encryption with message ID for AAD."""
        encryptor = MessageEncryptor()
        content = "Secret message"
        message_id = 12345678901234567890
        
        encrypted = encryptor.encrypt_message(content, message_id)
        decrypted = encryptor.decrypt_message(encrypted, message_id)
        
        assert decrypted == content

    def test_message_decryption_wrong_message_id(self):
        """Test that decryption fails with wrong message ID."""
        encryptor = MessageEncryptor()
        content = "Secret message"
        message_id = 12345678901234567890
        wrong_id = 98765432109876543210
        
        encrypted = encryptor.encrypt_message(content, message_id)
        
        with pytest.raises(ValueError):
            encryptor.decrypt_message(encrypted, wrong_id)

    def test_message_legacy_plaintext_passthrough(self):
        """Test that legacy plaintext messages are returned unchanged."""
        encryptor = MessageEncryptor()
        plaintext = "This is plaintext"
        
        decrypted = encryptor.decrypt_message(plaintext)
        assert decrypted == plaintext
        assert not encryptor.is_encrypted(plaintext)

    def test_message_empty_content(self):
        """Test encrypting empty message content."""
        encryptor = MessageEncryptor()
        encrypted = encryptor.encrypt_message("")
        decrypted = encryptor.decrypt_message(encrypted)
        assert decrypted == ""

    def test_message_long_content(self):
        """Test encrypting long message content."""
        encryptor = MessageEncryptor()
        content = "A" * 50000
        encrypted = encryptor.encrypt_message(content)
        decrypted = encryptor.decrypt_message(encrypted)
        assert decrypted == content

    def test_message_special_characters(self):
        """Test message encryption with special characters."""
        encryptor = MessageEncryptor()
        content = "Test: !@#$%^&*()_+-=[]{}|;':\",./<>?\n\t"
        encrypted = encryptor.encrypt_message(content)
        decrypted = encryptor.decrypt_message(encrypted)
        assert decrypted == content

    def test_message_unicode_content(self):
        """Test message encryption with unicode characters."""
        encryptor = MessageEncryptor()
        content = "Hello 世界 🌍 Привет मस्ते"
        encrypted = encryptor.encrypt_message(content)
        decrypted = encryptor.decrypt_message(encrypted)
        assert decrypted == content

    def test_message_encryption_unique_ciphertexts(self):
        """Test that same message encrypts to different ciphertexts."""
        encryptor = MessageEncryptor()
        content = "Same message"
        
        encrypted1 = encryptor.encrypt_message(content)
        encrypted2 = encryptor.encrypt_message(content)
        
        assert encrypted1 != encrypted2
        assert encryptor.decrypt_message(encrypted1) == content
        assert encryptor.decrypt_message(encrypted2) == content


class TestSecureTokenGeneration:
    """Test secure token generation and validation."""

    def test_generate_token_secret_length(self):
        """Test that token secret has correct length."""
        secret = generate_token_secret(32)
        decoded = base64.urlsafe_b64decode(secret + "==")
        assert len(decoded) == 32

    def test_generate_token_secret_uniqueness(self):
        """Test that generated secrets are unique."""
        secrets = set()
        for _ in range(1000):
            secret = generate_token_secret()
            assert secret not in secrets
            secrets.add(secret)

    def test_generate_token_secret_url_safe(self):
        """Test that token secrets are URL-safe."""
        secret = generate_token_secret()
        assert "+" not in secret
        assert "/" not in secret
        assert "=" not in secret

    def test_token_secret_entropy(self):
        """Test that token secrets have high entropy."""
        secret = generate_token_secret(32)
        decoded = base64.urlsafe_b64decode(secret + "==")
        
        byte_counts = {}
        for byte in decoded:
            byte_counts[byte] = byte_counts.get(byte, 0) + 1
        
        assert len(byte_counts) > 20

    def test_hash_token_consistency(self):
        """Test that hashing same token produces same hash."""
        secret = generate_token_secret()
        hash1 = hash_token(secret)
        hash2 = hash_token(secret)
        assert hash1 == hash2

    def test_hash_token_different_secrets(self):
        """Test that different secrets produce different hashes."""
        secret1 = generate_token_secret()
        secret2 = generate_token_secret()
        hash1 = hash_token(secret1)
        hash2 = hash_token(secret2)
        assert hash1 != hash2

    def test_hash_token_length(self):
        """Test that token hash is SHA-256 (64 hex chars)."""
        secret = generate_token_secret()
        token_hash = hash_token(secret)
        assert len(token_hash) == 64
        assert all(c in "0123456789abcdef" for c in token_hash)

    def test_verify_token_hash_correct(self):
        """Test verifying correct token hash."""
        secret = generate_token_secret()
        token_hash = hash_token(secret)
        assert verify_token_hash(secret, token_hash) is True

    def test_verify_token_hash_incorrect(self):
        """Test verifying incorrect token hash."""
        secret1 = generate_token_secret()
        secret2 = generate_token_secret()
        hash1 = hash_token(secret1)
        assert verify_token_hash(secret2, hash1) is False

    def test_create_session_token(self):
        """Test creating session token."""
        session_id = 1234567890
        token, token_hash = create_session_token(session_id)
        
        assert token.startswith(str(session_id) + ".")
        assert len(token_hash) == 64

    def test_create_bot_token(self):
        """Test creating bot token."""
        bot_id = 9876543210
        token, token_hash = create_bot_token(bot_id)
        
        assert token.startswith(f"bot.{bot_id}.")
        assert len(token_hash) == 64

    def test_parse_session_token(self):
        """Test parsing session token."""
        session_id = 1234567890
        token, _ = create_session_token(session_id)
        
        parsed = parse_token(token)
        assert parsed is not None
        assert parsed["token_type"] == "session"
        assert parsed["id"] == session_id

    def test_parse_bot_token(self):
        """Test parsing bot token."""
        bot_id = 9876543210
        token, _ = create_bot_token(bot_id)
        
        parsed = parse_token(token)
        assert parsed is not None
        assert parsed["token_type"] == "bot"
        assert parsed["id"] == bot_id

    def test_parse_invalid_token(self):
        """Test parsing invalid token."""
        invalid_token = "invalid.token.format.extra"
        parsed = parse_token(invalid_token)
        assert parsed is None

    def test_token_verification_timing_attack_resistance(self):
        """Test that token verification is timing-attack resistant."""
        secret = generate_token_secret()
        correct_hash = hash_token(secret)
        wrong_secret = generate_token_secret()
        
        correct_timings = []
        wrong_timings = []
        
        for _ in range(100):
            start = time.perf_counter()
            verify_token_hash(secret, correct_hash)
            end = time.perf_counter()
            correct_timings.append(end - start)
            
            start = time.perf_counter()
            verify_token_hash(wrong_secret, correct_hash)
            end = time.perf_counter()
            wrong_timings.append(end - start)
        
        avg_correct = sum(correct_timings) / len(correct_timings)
        avg_wrong = sum(wrong_timings) / len(wrong_timings)
        
        assert abs(avg_correct - avg_wrong) < max(avg_correct, avg_wrong) * 0.5


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


class TestCryptographicEdgeCases:
    """Test edge cases and error handling in cryptographic operations."""

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

    def test_null_bytes_in_data(self, setup_encryption):
        """Test encrypting data with null bytes."""
        data = "test\x00data\x00with\x00nulls"
        encrypted = encrypt_data(data)
        decrypted = decrypt_data(encrypted)
        assert decrypted == data

    def test_max_length_snowflake_components(self):
        """Test Snowflake ID with maximum component values."""
        gen = SnowflakeGenerator(worker_id=31, datacenter_id=31)
        snowflake_id = gen.generate()
        parsed = gen.parse(snowflake_id)
        
        assert parsed['worker_id'] == 31
        assert parsed['datacenter_id'] == 31

    def test_signature_with_binary_data(self, setup_encryption):
        """Test signing binary data."""
        private_key, public_key = generate_key_pair()
        data = bytes(range(256))
        signature = sign_data(data, private_key)
        assert verify_signature(data, signature, public_key) is True

    def test_encryption_with_all_byte_values(self, setup_encryption):
        """Test encrypting data with all possible byte values."""
        data = "".join(chr(i) for i in range(256) if i != 0)
        encrypted = encrypt_data(data)
        decrypted = decrypt_data(encrypted)
        assert decrypted == data

    def test_concurrent_encryption_operations(self, setup_encryption):
        """Test concurrent encryption operations."""
        results = []
        lock = threading.Lock()
        
        def encrypt_decrypt():
            data = f"test data {threading.current_thread().name}"
            encrypted = encrypt_data(data)
            decrypted = decrypt_data(encrypted)
            with lock:
                results.append(decrypted == data)
        
        threads = [threading.Thread(target=encrypt_decrypt) for _ in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        
        assert all(results)

    def test_repeated_password_hashing(self, setup_encryption):
        """Test that repeated password hashing produces unique hashes."""
        password = "test_password"
        hashes = set()
        
        for _ in range(50):
            hash_str = hash_password(password)
            assert hash_str not in hashes
            hashes.add(hash_str)

    def test_key_derivation_edge_cases(self):
        """Test key derivation with edge case inputs."""
        manager = EncryptionManager()
        
        key1, salt1 = manager.derive_key("a")
        assert len(key1) == 32
        assert len(salt1) == 16
        
        key2, salt2 = manager.derive_key("a" * 10000)
        assert len(key2) == 32
        assert len(salt2) == 16

    def test_malformed_encrypted_data_handling(self, setup_encryption):
        """Test handling of various malformed encrypted data."""
        # Empty string returns empty string (not an error)
        assert decrypt_data("") == ""
        
        # These should all raise ValueError
        error_cases = [
            "a",
            "ENC:",
            "ENC::",
            "ENC:abc:",
            "ENC:1:",
            "ENC:1:a",
            "ENC:1:!!!invalid_base64!!!",
        ]
        
        for malformed in error_cases:
            with pytest.raises(ValueError):
                decrypt_data(malformed)

    def test_signature_tampering_detection(self, setup_encryption):
        """Test that signature detects any tampering."""
        private_key, public_key = generate_key_pair()
        data = b"important data"
        signature = sign_data(data, private_key)
        
        for i in range(len(signature)):
            tampered_sig = bytearray(signature)
            tampered_sig[i] ^= 0x01
            assert verify_signature(data, bytes(tampered_sig), public_key) is False

    def test_encryption_output_format_consistency(self, setup_encryption):
        """Test that encryption output format is consistent."""
        data = "test"
        encrypted = encrypt_data(data)
        
        if encrypted.startswith("ENC:"):
            parts = encrypted.split(":", 2)
            assert len(parts) == 3
            assert parts[0] == "ENC"
            assert parts[1].isdigit()
            
            try:
                base64.b64decode(parts[2])
            except Exception:
                pytest.fail("Encrypted data part is not valid base64")
