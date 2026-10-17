"""
Tests for KEK migration utilities and the KeyringMigration lifecycle.

A real AESGCM-encrypted keyring file is built on disk per test so the
validate/backup/migrate/rollback flows run against genuine data.
"""

import base64
import json
import os

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.utils.encryption.kek_migration.migration import KeyringMigration
from src.utils.encryption.kek_migration.utils import (
    decode_env_key,
    get_keyring_paths,
)

OLD_KEK = bytes(range(32))
NEW_KEK = bytes(range(32, 64))


def _encrypt_keyring(kek: bytes, data: dict) -> dict:
    aesgcm = AESGCM(kek)
    nonce = os.urandom(12)
    payload = aesgcm.encrypt(nonce, json.dumps(data).encode(), None)
    return {
        "nonce": base64.b64encode(nonce).decode(),
        "payload": base64.b64encode(payload).decode(),
    }


def _write_keyring(path, kek=OLD_KEK, key_count=2):
    data = {
        "keys": {f"k{i}": {"version": i} for i in range(1, key_count + 1)},
        "current_version": 1,
        "rotated_at": 12345,
    }
    path.write_text(json.dumps(_encrypt_keyring(kek, data)))
    return data


class TestDecodeEnvKey:
    def test_hex_key(self):
        key = "ab" * 32
        assert decode_env_key(key) == bytes.fromhex(key)

    def test_base64_key(self):
        raw = os.urandom(32)
        assert decode_env_key(base64.b64encode(raw).decode()) == raw

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="32-byte key"):
            decode_env_key("garbage")


class TestGetKeyringPaths:
    def test_returns_only_existing_keyrings(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "pathlib.Path.home",
            classmethod(lambda cls: tmp_path),
        )
        (tmp_path / ".plexichat" / "data").mkdir(parents=True)
        (tmp_path / ".plexichat" / "data" / "system_keyring.json").write_text("{}")
        (tmp_path / ".plexichat" / "data" / "message_keyring.json").write_text("{}")

        paths = get_keyring_paths()

        assert [p.name for p in paths] == [
            "system_keyring.json",
            "message_keyring.json",
        ]


@pytest.fixture
def migrator(tmp_path):
    keyring_path = tmp_path / "system_keyring.json"
    _write_keyring(keyring_path)
    return KeyringMigration(keyring_path)


class TestValidateKeyring:
    def test_valid_with_old_kek(self, migrator):
        result = migrator.validate_keyring(OLD_KEK)
        assert result["keyring_exists"] is True
        assert result["can_decrypt_with_old_kek"] is True
        assert result["can_decrypt_with_new_kek"] is False
        assert result["key_count"] == 2
        assert result["current_version"] == 1
        assert result["rotated_at"] == 12345
        assert result["validation_errors"] == []

    def test_wrong_kek_fails(self, migrator):
        result = migrator.validate_keyring(NEW_KEK)
        assert result["can_decrypt_with_old_kek"] is False
        assert len(result["validation_errors"]) == 1
        assert "Failed to decrypt with old KEK" in result["validation_errors"][0]

    def test_new_kek_that_decrypts_reported(self, migrator, tmp_path):
        other = tmp_path / "other.json"
        _write_keyring(other, kek=NEW_KEK)
        m = KeyringMigration(other)
        result = m.validate_keyring(OLD_KEK, NEW_KEK)
        assert result["can_decrypt_with_old_kek"] is False
        assert result["can_decrypt_with_new_kek"] is True

    def test_missing_keyring(self, tmp_path):
        m = KeyringMigration(tmp_path / "missing.json")
        result = m.validate_keyring(OLD_KEK)
        assert result["keyring_exists"] is False
        assert "does not exist" in result["validation_errors"][0]

    def test_corrupt_keyring(self, tmp_path):
        path = tmp_path / "corrupt.json"
        path.write_text("{not-json")
        result = KeyringMigration(path).validate_keyring(OLD_KEK)
        assert result["can_decrypt_with_old_kek"] is False
        assert len(result["validation_errors"]) >= 1


class TestBackupRestore:
    def test_backup_creates_copy(self, migrator):
        assert migrator.backup_keyring() is True
        assert migrator.backup_path.exists()
        assert migrator.backup_path.read_bytes() == migrator.keyring_path.read_bytes()

    def test_backup_missing_keyring_false(self, tmp_path):
        m = KeyringMigration(tmp_path / "missing.json")
        assert m.backup_keyring() is False

    def test_restore_backup(self, migrator):
        original = migrator.keyring_path.read_bytes()
        migrator.backup_keyring()
        migrator.keyring_path.write_bytes(b"tampered")
        assert migrator.restore_backup() is True
        assert migrator.keyring_path.read_bytes() == original

    def test_restore_without_backup_false(self, migrator):
        assert migrator.restore_backup() is False


class TestMigrateKek:
    def test_migrate_success(self, migrator):
        assert migrator.migrate_kek(OLD_KEK, NEW_KEK) is True
        # New KEK decrypts, old no longer does.
        assert migrator.validate_keyring(NEW_KEK)["can_decrypt_with_old_kek"] is True
        assert migrator.validate_keyring(OLD_KEK)["can_decrypt_with_old_kek"] is False
        # Backup + metadata cleaned up on success.
        assert not migrator.backup_path.exists()
        assert not migrator.metadata_path.exists()

    def test_migrate_preserves_key_data(self, migrator):
        assert migrator.migrate_kek(OLD_KEK, NEW_KEK) is True
        result = migrator.validate_keyring(NEW_KEK)
        assert result["key_count"] == 2
        assert result["current_version"] == 1

    def test_migrate_wrong_old_kek_false(self, migrator):
        assert migrator.migrate_kek(NEW_KEK, OLD_KEK) is False

    def test_migrate_already_new_kek_requires_force(self, tmp_path):
        path = tmp_path / "k.json"
        _write_keyring(path, kek=NEW_KEK)
        m = KeyringMigration(path)
        # When the file already decrypts with the target KEK (same key
        # supplied as old and new), a plain migration refuses to
        # re-encrypt but --force proceeds.
        assert m.migrate_kek(NEW_KEK, NEW_KEK) is False
        assert m.migrate_kek(NEW_KEK, NEW_KEK, force=True) is True

    def test_dry_run_returns_true_without_touching_file(self, tmp_path):
        path = tmp_path / "k.json"
        _write_keyring(path)
        original = path.read_bytes()
        m = KeyringMigration(path, dry_run=True)
        assert m.migrate_kek(OLD_KEK, NEW_KEK) is True
        assert path.read_bytes() == original

    def test_failed_migration_triggers_rollback(self, migrator):
        # Corrupt the keyring after construction so the decrypt step fails.
        migrator.keyring_path.write_text("{broken")
        assert migrator.migrate_kek(OLD_KEK, NEW_KEK) is False


class TestRollback:
    def test_rollback_restores_and_cleans(self, migrator):
        original = migrator.keyring_path.read_bytes()
        migrator.backup_keyring()
        migrator.temp_path.write_text("x")
        migrator.rollback_path.write_text("x")
        migrator.metadata_path.write_text("x")

        assert migrator.rollback() is True
        assert migrator.keyring_path.read_bytes() == original
        assert not migrator.temp_path.exists()
        assert not migrator.rollback_path.exists()
        assert not migrator.metadata_path.exists()

    def test_rollback_without_backup_false(self, migrator):
        assert migrator.rollback() is False


class TestCleanup:
    def test_cleanup_removes_temp_files(self, migrator):
        migrator.temp_path.write_text("t")
        migrator.rollback_path.write_text("r")
        migrator.metadata_path.write_text("m")
        migrator.cleanup_temp_files()
        assert not migrator.temp_path.exists()
        assert not migrator.rollback_path.exists()
        assert not migrator.metadata_path.exists()


class TestKekSourceDescription:
    def test_env_var_match(self, migrator, monkeypatch):
        monkeypatch.setenv("PLEXICHAT_SYSTEM_KEY", OLD_KEK.hex())
        desc = migrator._kek_source_description(OLD_KEK)
        assert desc == "Environment variable PLEXICHAT_SYSTEM_KEY (hex)"

    def test_env_var_base64_match(self, migrator, monkeypatch):
        monkeypatch.setenv("PLEXICHAT_MESSAGE_KEY", base64.b64encode(NEW_KEK).decode())
        desc = migrator._kek_source_description(NEW_KEK)
        assert desc == "Environment variable PLEXICHAT_MESSAGE_KEY (Base64)"

    def test_unknown_source(self, migrator):
        assert migrator._kek_source_description(os.urandom(32)) == "Unknown source"
