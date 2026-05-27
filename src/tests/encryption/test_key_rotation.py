"""
Advanced key rotation tests for encryption system.

Tests cover key rotation lifecycle, migration scenarios,
concurrent access, and data integrity across rotations.
"""

import pytest
import os
import time
import threading

# common-utils is now a native package.

from src.utils.encryption.core import (  # noqa: E402
    EncryptionManager,
    MessageEncryptor,
    Keyring,
)


@pytest.fixture
def temp_keyring_path(tmp_path):
    """Create a temporary keyring path for testing."""
    return tmp_path / "test_keyring.json"


@pytest.fixture
def manager_with_keyring(temp_keyring_path):
    """Create a manager with a fresh keyring."""
    manager = EncryptionManager()
    manager.keyring = Keyring(temp_keyring_path)
    return manager


class TestKeyRotationLifecycle:
    """Test complete key rotation lifecycle."""

    def test_initial_key_generation(self, temp_keyring_path):
        """Test that initial key is generated automatically."""
        keyring = Keyring(temp_keyring_path)
        version, key = keyring.get_key()

        assert version == 1
        assert len(key) == 32
        assert keyring.current_version == 1

    def test_rotation_increments_version(self, temp_keyring_path):
        """Test that rotation increments version number."""
        keyring = Keyring(temp_keyring_path)
        keyring.get_key()

        v1 = keyring.rotate()
        v2 = keyring.rotate()
        v3 = keyring.rotate()

        assert v1 == 2
        assert v2 == 3
        assert v3 == 4

    def test_old_keys_remain_accessible(self, temp_keyring_path):
        """Test that old keys remain accessible after rotation."""
        keyring = Keyring(temp_keyring_path)

        _, key_v1 = keyring.get_key()
        keyring.rotate()
        _, key_v2 = keyring.get_key()
        keyring.rotate()
        _, key_v3 = keyring.get_key()

        _, retrieved_v1 = keyring.get_key(version=1)
        _, retrieved_v2 = keyring.get_key(version=2)
        _, retrieved_v3 = keyring.get_key(version=3)

        assert retrieved_v1 == key_v1
        assert retrieved_v2 == key_v2
        assert retrieved_v3 == key_v3

    def test_decrypt_old_data_after_multiple_rotations(self, manager_with_keyring):
        """Test decrypting data encrypted with old keys after multiple rotations."""
        data1 = "data encrypted with v1"
        encrypted1 = manager_with_keyring.encrypt_data(data1)

        manager_with_keyring.keyring.rotate()
        data2 = "data encrypted with v2"
        encrypted2 = manager_with_keyring.encrypt_data(data2)

        manager_with_keyring.keyring.rotate()
        data3 = "data encrypted with v3"
        encrypted3 = manager_with_keyring.encrypt_data(data3)

        assert manager_with_keyring.decrypt_data(encrypted1) == data1
        assert manager_with_keyring.decrypt_data(encrypted2) == data2
        assert manager_with_keyring.decrypt_data(encrypted3) == data3

    def test_rotation_persistence(self, temp_keyring_path):
        """Test that rotated keys persist across keyring instances."""
        keyring1 = Keyring(temp_keyring_path)
        keyring1.get_key()
        keyring1.rotate()
        keyring1.rotate()
        keys_before = dict(keyring1.keys)
        version_before = keyring1.current_version

        keyring2 = Keyring(temp_keyring_path)
        assert keyring2.current_version == version_before
        assert keyring2.keys == keys_before


class TestKeyRotationMigration:
    """Test migration scenarios during key rotation."""

    def test_legacy_key_migration(self, tmp_path):
        """Test migration from legacy single-key file to keyring."""
        legacy_path = tmp_path / "data" / ".encryption_key"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_key = os.urandom(32)
        with open(legacy_path, "wb") as f:
            f.write(legacy_key)

        keyring_path = tmp_path / "data" / "keyring.json"
        keyring = Keyring(keyring_path)
        manager = EncryptionManager()
        manager.keyring = keyring

        data = "test data"
        encrypted = manager.encrypt_data(data)
        decrypted = manager.decrypt_data(encrypted)

        assert decrypted == data

    def test_message_key_migration(self, tmp_path):
        """Test migration from legacy message key file."""
        legacy_msg_path = tmp_path / "data" / ".message_encryption_key"
        legacy_msg_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_msg_key = os.urandom(32)
        with open(legacy_msg_path, "wb") as f:
            f.write(legacy_msg_key)

        keyring_path = tmp_path / "data" / "keyring.json"
        encryptor = MessageEncryptor(Keyring(keyring_path))

        content = "test message"
        encrypted = encryptor.encrypt_message(content)
        decrypted = encryptor.decrypt_message(encrypted)

        assert decrypted == content

    def test_re_encrypt_with_new_key(self, manager_with_keyring):
        """Test re-encrypting data with new key after rotation."""
        original_data = "sensitive data"
        old_encrypted = manager_with_keyring.encrypt_data(original_data)

        manager_with_keyring.keyring.rotate()

        decrypted = manager_with_keyring.decrypt_data(old_encrypted)
        new_encrypted = manager_with_keyring.encrypt_data(decrypted)

        assert manager_with_keyring.decrypt_data(new_encrypted) == original_data
        assert old_encrypted != new_encrypted


class TestConcurrentKeyRotation:
    """Test concurrent access during key rotation."""

    def test_concurrent_reads_during_rotation(self, manager_with_keyring):
        """Test that concurrent reads work during rotation."""
        data_list = [f"data {i}" for i in range(20)]
        encrypted_list = [manager_with_keyring.encrypt_data(d) for d in data_list]

        results = []
        errors = []
        lock = threading.Lock()

        def read_and_decrypt():
            try:
                for encrypted in encrypted_list:
                    decrypted = manager_with_keyring.decrypt_data(encrypted)
                    with lock:
                        results.append(decrypted)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=read_and_decrypt) for _ in range(5)]

        for thread in threads:
            thread.start()

        manager_with_keyring.keyring.rotate()

        for thread in threads:
            thread.join()

        assert len(errors) == 0
        assert len(results) == 100

    def test_concurrent_writes_during_rotation(self, manager_with_keyring):
        """Test that concurrent writes work during rotation."""
        results = []
        errors = []
        lock = threading.Lock()

        def encrypt_data_thread(data):
            try:
                encrypted = manager_with_keyring.encrypt_data(data)
                decrypted = manager_with_keyring.decrypt_data(encrypted)
                with lock:
                    results.append(decrypted == data)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = []
        for i in range(20):
            thread = threading.Thread(target=encrypt_data_thread, args=(f"data {i}",))
            threads.append(thread)
            thread.start()

        time.sleep(0.01)
        manager_with_keyring.keyring.rotate()

        for thread in threads:
            thread.join()

        assert len(errors) == 0
        assert all(results)

    def test_thread_safety_of_rotation(self, temp_keyring_path):
        """Test that rotation itself is thread-safe."""
        keyring = Keyring(temp_keyring_path)
        keyring.get_key()

        versions = []
        errors = []
        lock = threading.Lock()

        def rotate_key():
            try:
                version = keyring.rotate()
                with lock:
                    versions.append(version)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=rotate_key) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # No errors should occur during concurrent rotation
        assert len(errors) == 0
        # All 10 rotations should complete
        assert len(versions) == 10
        # Final version should be 11 (started at 1, rotated 10 times)
        assert keyring.current_version == 11


class TestKeyRotationDataIntegrity:
    """Test data integrity across key rotations."""

    def test_bulk_data_integrity_after_rotation(self, manager_with_keyring):
        """Test that large amounts of data remain intact after rotation."""
        data_map = {}

        for i in range(100):
            data = f"data item {i}: {'x' * 100}"
            encrypted = manager_with_keyring.encrypt_data(data)
            data_map[encrypted] = data

        manager_with_keyring.keyring.rotate()

        for encrypted, original in data_map.items():
            decrypted = manager_with_keyring.decrypt_data(encrypted)
            assert decrypted == original

    def test_message_integrity_across_rotations(self, temp_keyring_path):
        """Test that messages remain intact across key rotations."""
        encryptor = MessageEncryptor(Keyring(temp_keyring_path))

        messages = []
        for i in range(50):
            content = f"Message {i}: important content"
            message_id = 1000 + i
            encrypted = encryptor.encrypt_message(content, message_id)
            messages.append((encrypted, content, message_id))

        encryptor.keyring.rotate()
        encryptor.keyring.rotate()

        for encrypted, original_content, message_id in messages:
            decrypted = encryptor.decrypt_message(encrypted, message_id)
            assert decrypted == original_content

    def test_mixed_version_decryption(self, manager_with_keyring):
        """Test decrypting data encrypted with different key versions."""
        encrypted_data = []

        for version in range(1, 6):
            data = f"data from version {version}"
            encrypted = manager_with_keyring.encrypt_data(data)
            encrypted_data.append((encrypted, data))
            manager_with_keyring.keyring.rotate()

        for encrypted, original_data in encrypted_data:
            decrypted = manager_with_keyring.decrypt_data(encrypted)
            assert decrypted == original_data


class TestKeyRotationErrorHandling:
    """Test error handling during key rotation."""

    def test_invalid_key_version_request(self, temp_keyring_path):
        """Test requesting non-existent key version."""
        keyring = Keyring(temp_keyring_path)
        keyring.get_key()

        with pytest.raises(ValueError, match="Key version .* not found"):
            keyring.get_key(version=99)

    def test_decrypt_with_missing_key_version(self, manager_with_keyring):
        """Test decrypting data when key version is missing."""
        manager_with_keyring.keyring.get_key()

        fake_encrypted = "ENC:99:YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXoxMjM0NTY3ODkw"

        with pytest.raises(ValueError, match="Key version .* not found"):
            manager_with_keyring.decrypt_data(fake_encrypted)

    def test_keyring_corruption_recovery(self, temp_keyring_path):
        """Test that keyring handles corrupted file gracefully."""
        # Keyring now raises error on corruption instead of silently resetting
        # This test verifies that the error is raised correctly
        from src.utils.encryption.core import Keyring, KeyringDecryptionError

        # Create a corrupted keyring file
        with open(temp_keyring_path, "w") as f:
            f.write("corrupted data")

        # Should raise KeyringDecryptionError
        with pytest.raises(KeyringDecryptionError):
            Keyring(temp_keyring_path)


class TestAutomaticKeyRotation:
    """Test automatic key rotation based on configuration."""

    def test_rotation_based_on_time(self, manager_with_keyring):
        """Test that rotation can be triggered based on time elapsed."""
        manager_with_keyring.keyring.get_key()
        initial_version = manager_with_keyring.keyring.current_version

        # Set rotated_at to 200 days ago (default rotation interval is 180 days)
        manager_with_keyring.keyring.rotated_at = int(time.time()) - (200 * 86400)

        rotated = manager_with_keyring.rotate_keys()
        assert rotated is True
        assert manager_with_keyring.keyring.current_version == initial_version + 1

    def test_no_rotation_when_recent(self, manager_with_keyring):
        """Test that rotation doesn't occur if key is recent."""
        manager_with_keyring.keyring.get_key()
        initial_version = manager_with_keyring.keyring.current_version

        rotated = manager_with_keyring.rotate_keys()
        assert rotated is False
        assert manager_with_keyring.keyring.current_version == initial_version

    def test_force_rotation(self, manager_with_keyring):
        """Test forcing rotation regardless of time."""
        manager_with_keyring.keyring.get_key()
        initial_version = manager_with_keyring.keyring.current_version

        rotated = manager_with_keyring.rotate_keys(force=True)
        assert rotated is True
        assert manager_with_keyring.keyring.current_version == initial_version + 1
