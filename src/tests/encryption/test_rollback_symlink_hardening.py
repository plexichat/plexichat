"""Regression: KEK-migration rollback/restore must not follow a planted symlink.

The migrate and backup writers were hardened to install their output via an
O_EXCL temp file + ``os.replace`` (never following a symlink at the
destination). The rollback path was missed: ``restore_backup`` and ``rollback``
used ``shutil.copy2(backup, keyring_path)``, which follows a symlink planted at
the destination and overwrites the link target with the backup's key material.
A local attacker with write access to the keyring directory could redirect a
rollback to clobber an arbitrary file, defeating the earlier hardening.
"""

import os
import stat

import pytest

from src.utils.encryption.kek_migration.migration import (
    KEKMigrationError,
    KeyringMigration,
)


@pytest.fixture
def migration(tmp_path):
    keyring = tmp_path / "system_keyring.json"
    keyring.write_bytes(b"ORIGINAL-KEYRING")
    m = KeyringMigration(keyring)
    assert m.backup_keyring() is True
    return m


@pytest.mark.unit
def test_restore_backup_replaces_destination_symlink(migration, tmp_path):
    """Restore must replace a symlink at the keyring path, not write through it."""
    victim = tmp_path / "victim.json"
    sentinel = b"DO NOT TOUCH"
    victim.write_bytes(sentinel)

    backup_bytes = migration.backup_path.read_bytes()
    migration.keyring_path.unlink()
    migration.keyring_path.symlink_to(victim)

    assert migration.restore_backup() is True

    # The link target was never touched; the link itself became a real, private
    # keyring holding the backup bytes.
    assert victim.read_bytes() == sentinel
    assert not migration.keyring_path.is_symlink()
    assert migration.keyring_path.read_bytes() == backup_bytes
    if os.name == "posix":
        assert stat.S_IMODE(migration.keyring_path.stat().st_mode) == 0o600


@pytest.mark.unit
def test_rollback_replaces_destination_symlink(migration, tmp_path):
    """``rollback()`` takes the same symlink-safe path as ``restore_backup``."""
    victim = tmp_path / "victim.json"
    sentinel = b"DO NOT TOUCH"
    victim.write_bytes(sentinel)

    backup_bytes = migration.backup_path.read_bytes()
    migration.keyring_path.unlink()
    migration.keyring_path.symlink_to(victim)

    assert migration.rollback() is True

    assert victim.read_bytes() == sentinel
    assert not migration.keyring_path.is_symlink()
    assert migration.keyring_path.read_bytes() == backup_bytes
    if os.name == "posix":
        assert stat.S_IMODE(migration.keyring_path.stat().st_mode) == 0o600


@pytest.mark.unit
def test_restore_refuses_symlinked_backup_source(migration, tmp_path):
    """A backup path swapped for a symlink must not be read through."""
    victim = tmp_path / "victim.json"
    sentinel = b"ATTACKER-CONTROLLED"
    victim.write_bytes(sentinel)
    migration.backup_path.unlink()
    migration.backup_path.symlink_to(victim)

    assert migration.restore_backup() is False
    # The keyring was left untouched (no attacker bytes installed).
    assert migration.keyring_path.read_bytes() == b"ORIGINAL-KEYRING"

    # The low-level helper fails closed with a clear error.
    with pytest.raises(KEKMigrationError, match="symlinked backup"):
        migration._read_backup_bytes()
