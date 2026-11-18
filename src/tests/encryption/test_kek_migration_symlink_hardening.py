"""Regressions: KEK migration backup/re-encryption must not follow planted symlinks.

The old writer used fixed destination names (``*_temp.json`` for the
re-encrypted keyring, a second-precision timestamped ``*_backup_*.json``) with
``open(..., "w")`` / ``shutil.copy2``. Both follow symlinks, so another local
user with write access to the keyring directory could redirect a migration to
overwrite an unrelated file with key material and reset its mode to 0600. A
failed backup write could also leave a truncated, unusable rollback copy.
"""

import base64
import json
import os
import stat

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.utils.encryption.kek_migration.migration import KeyringMigration

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


def _write_keyring(path, kek: bytes = OLD_KEK) -> dict:
    data = {
        "keys": {"k1": {"version": 1}},
        "current_version": 1,
        "rotated_at": 12345,
    }
    path.write_text(json.dumps(_encrypt_keyring(kek, data)))
    return data


@pytest.fixture
def migrator(tmp_path):
    keyring_path = tmp_path / "system_keyring.json"
    _write_keyring(keyring_path)
    return KeyringMigration(keyring_path)


@pytest.mark.unit
def test_migrate_does_not_write_through_planted_temp_symlink(migrator, tmp_path):
    victim = tmp_path / "victim.json"
    sentinel = b"DO NOT TOUCH"
    victim.write_bytes(sentinel)
    planted = migrator.temp_path
    planted.symlink_to(victim)

    assert migrator.migrate_kek(OLD_KEK, NEW_KEK) is True

    # The link target was never touched and the link itself was not replaced.
    assert victim.read_bytes() == sentinel
    assert planted.is_symlink()
    # The real keyring was migrated and is still private.
    assert migrator.validate_keyring(NEW_KEK)["can_decrypt_with_old_kek"] is True
    assert migrator.validate_keyring(OLD_KEK)["can_decrypt_with_old_kek"] is False
    if os.name == "posix":
        assert stat.S_IMODE(migrator.keyring_path.stat().st_mode) == 0o600


@pytest.mark.unit
def test_backup_does_not_write_through_planted_symlink(migrator, tmp_path):
    original = migrator.keyring_path.read_bytes()
    victim = tmp_path / "victim.json"
    sentinel = b"DO NOT TOUCH"
    victim.write_bytes(sentinel)
    migrator.backup_path.symlink_to(victim)

    assert migrator.backup_keyring() is True

    assert victim.read_bytes() == sentinel
    # The symlink was replaced by a real, private backup of the keyring.
    assert not migrator.backup_path.is_symlink()
    assert migrator.backup_path.read_bytes() == original
    if os.name == "posix":
        assert stat.S_IMODE(migrator.backup_path.stat().st_mode) == 0o600

    # The backup remains usable for rollback.
    migrator.keyring_path.write_bytes(b"tampered")
    assert migrator.restore_backup() is True
    assert migrator.keyring_path.read_bytes() == original


@pytest.mark.unit
def test_temp_cleanup_does_not_delete_symlink_target(migrator, tmp_path):
    """Cleanup must unlink the planted symlink itself, never its target."""
    victim = tmp_path / "victim.json"
    sentinel = b"DO NOT TOUCH"
    victim.write_bytes(sentinel)
    planted = migrator.temp_path
    planted.symlink_to(victim)

    assert migrator.migrate_kek(OLD_KEK, NEW_KEK) is True
    migrator.cleanup_temp_files()

    assert victim.read_bytes() == sentinel
    assert not planted.exists()
