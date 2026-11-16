"""Performance regression test for migration directory discovery caching."""

import os

import pytest

from src.core.migrations import validator


@pytest.mark.migrations
class TestMigrationFileScanCache:
    def test_listing_cached_until_directory_changes(self, tmp_path, monkeypatch):
        validator._clear_files_cache()
        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "007_first.py").write_text(
            "def up(db): pass\ndef down(db): pass\n"
        )

        real_listdir = os.listdir
        calls = []

        def counting_listdir(path):
            if os.fspath(path) == os.fspath(migrations_dir):
                calls.append(path)
            return real_listdir(path)

        monkeypatch.setattr(validator.os, "listdir", counting_listdir)

        first = validator.get_migration_files(str(migrations_dir))
        assert [version for version, _ in first] == ["007"]

        second = validator.get_migration_files(str(migrations_dir))
        assert second == first
        assert len(calls) == 1, "unchanged migrations directory was rescanned"

        (migrations_dir / "008_second.py").write_text(
            "def up(db): pass\ndef down(db): pass\n"
        )
        third = validator.get_migration_files(str(migrations_dir))
        assert [version for version, _ in third] == ["007", "008"]
        assert len(calls) == 2, "directory mtime change did not invalidate cache"

    def test_returned_list_is_independent(self, tmp_path):
        validator._clear_files_cache()
        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "009_third.py").write_text(
            "def up(db): pass\ndef down(db): pass\n"
        )
        first = validator.get_migration_files(str(migrations_dir))
        first.append(("999", "bogus"))
        second = validator.get_migration_files(str(migrations_dir))
        assert [version for version, _ in second] == ["009"]
