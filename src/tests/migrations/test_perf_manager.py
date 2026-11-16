"""Performance regression test for cached migration integrity checks."""

from unittest.mock import MagicMock

import pytest

from src.core.migrations import manager as manager_module
from src.core.migrations import validator
from src.core.migrations.manager import MigrationManager


@pytest.mark.migrations
class TestIntegrityCheckCache:
    def test_integrity_recomputed_only_when_inputs_change(
        self, tmp_path, db, monkeypatch
    ):
        manager_module._clear_integrity_cache()
        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        target = migrations_dir / "000_test.py"
        content = b"def up(db): pass\ndef down(db): pass\n"
        target.write_bytes(content)

        checksum = validator.calculate_checksum(content)
        manager = MigrationManager(db)
        manager.migrations_dir = migrations_dir
        manager.tracker = MagicMock()
        manager.tracker.ensure_table_exists.return_value = None
        manager.tracker.get_all_migration_records.return_value = [
            {"version": "000", "status": "completed", "checksum": checksum}
        ]

        calls = []
        original = validator.calculate_checksum

        def counting(data):
            calls.append(data)
            return original(data)

        monkeypatch.setattr(validator, "calculate_checksum", counting)

        first = manager.validate_migration_integrity()
        assert first["valid"] is True
        assert first["checked"] == 1
        assert len(calls) == 1

        second = manager.validate_migration_integrity()
        assert second == first
        assert len(calls) == 1, "unchanged inputs were re-hashed"

        # Editing a migration file changes its mtime/size, so the verdict must
        # be recomputed and the checksum mismatch surfaced.
        target.write_bytes(content + b"\n# edited\n")
        third = manager.validate_migration_integrity()
        assert third["valid"] is False
        assert len(calls) == 2
