"""Regressions for migration integrity fail-open / checksum-bypass hardening.

Two weaknesses are pinned here:

* The integrity verdict cache used to be keyed only on file mtime and size.
  Both are attacker-controllable metadata, so a same-size rewrite that restores
  the mtime kept serving a cached ``valid=True`` verdict without re-hashing the
  tampered file.
* ``_execute_migration`` used to swallow any read/hash error in its checksum
  tamper guard (log a warning, then execute anyway), i.e. it failed open.
"""

import os
from unittest.mock import MagicMock

import pytest

from src.core.migrations import manager as manager_module
from src.core.migrations import validator
from src.core.migrations.manager import Migration, MigrationManager

_MIGRATION_SOURCE = b"def up(db): pass\ndef down(db): pass\n"


def _manager_with_recorded_file(tmp_path, content=_MIGRATION_SOURCE):
    """Manager whose tracker reports one completed migration for a temp file."""
    manager_module._clear_integrity_cache()
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    target = migrations_dir / "000_probe.py"
    target.write_bytes(content)

    db = MagicMock()
    db.type = "sqlite"
    db.config = {"type": "sqlite", "path": ":memory:"}

    manager = MigrationManager(db)
    manager.migrations_dir = migrations_dir
    manager.tracker = MagicMock()
    manager.tracker.ensure_table_exists.return_value = None
    manager.tracker.get_all_migration_records.return_value = [
        {
            "version": "000",
            "status": "completed",
            "checksum": validator.calculate_checksum(content),
        }
    ]
    return manager, target


@pytest.mark.migrations
def test_same_size_same_mtime_tampering_invalidates_integrity_cache(tmp_path):
    """A metadata-preserving rewrite must still be detected as tampering."""
    manager, target = _manager_with_recorded_file(tmp_path)

    first = manager.validate_migration_integrity()
    assert first["valid"] is True
    assert first["checked"] == 1

    # Rewrite the file with different bytes of the EXACT same length and put the
    # original mtime back. The old (mtime, size) stamp would be identical here.
    before = target.stat()
    tampered = _MIGRATION_SOURCE.replace(b"pass", b"P@ss", 1)
    assert len(tampered) == len(_MIGRATION_SOURCE)
    target.write_bytes(tampered)
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))

    after = target.stat()
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns

    second = manager.validate_migration_integrity()
    assert second["valid"] is False
    assert [m["version"] for m in second["mismatches"]] == ["000"]
    assert "Checksum mismatch" in second["mismatches"][0]["error"]


@pytest.mark.migrations
def test_checksum_read_failure_fails_closed_before_execution(tmp_path, monkeypatch):
    """An unreadable/unhashable file must block the migration, not skip the guard."""
    manager, target = _manager_with_recorded_file(tmp_path)
    manager.tracker.get_migration_status.return_value = {
        "version": "000",
        "status": "failed",
        "checksum": "recorded-digest",
    }

    def boom(_content):
        raise OSError("simulated disk read failure")

    monkeypatch.setattr(validator, "calculate_checksum", boom)
    executed = []
    monkeypatch.setattr(
        manager_module.MigrationRunner,
        "validate_migration_file",
        lambda self, path: executed.append(path),
    )

    migration = Migration("000", "probe", str(target), "recorded-digest")
    with pytest.raises(ValueError, match="Cannot verify checksum"):
        manager._execute_migration(migration, dry_run=False)

    assert executed == [], (
        "migration module was validated/executed without a verified checksum"
    )
    manager.tracker.record_migration_start.assert_not_called()
    manager.tracker.record_migration_success.assert_not_called()
    manager.tracker.record_migration_failure.assert_called_once()


@pytest.mark.migrations
def test_missing_recorded_checksum_fails_closed(tmp_path):
    """A history row without a checksum cannot be used as a verification baseline."""
    manager, target = _manager_with_recorded_file(tmp_path)
    manager.tracker.get_migration_status.return_value = {
        "version": "000",
        "status": "failed",
        "checksum": None,
    }

    migration = Migration("000", "probe", str(target), "recorded-digest")
    with pytest.raises(ValueError, match="no checksum to verify"):
        manager._execute_migration(migration, dry_run=False)

    manager.tracker.record_migration_start.assert_not_called()
