"""Focused regression tests for the database/migration/fts hardening wave.

These guard the boundaries added at the SQL/migration/indexer layer: resource
sizes that must be bounded, identifiers that must be constrained before they
are interpolated, and queries whose complexity must be capped.
"""

import pytest

from src.core.security.hardening import HardeningViolation


class TestPoolSizeBounds:
    def test_rejects_unbounded_min_size(self):
        from src.core.database.engines.pool import ConnectionPool

        created = []
        with pytest.raises(HardeningViolation):
            ConnectionPool(lambda: created.append(1), 10**9, 10**9 + 1)
        assert created == []

    def test_rejects_min_greater_than_max(self):
        from src.core.database.engines.pool import ConnectionPool

        with pytest.raises(ValueError, match="min_size cannot exceed max_size"):
            ConnectionPool(lambda: object(), 5, 2)


class TestIdentifierBound:
    def test_rejects_overlong_identifier(self):
        from src.core.database.dialect import sanitize_identifier

        with pytest.raises(HardeningViolation):
            sanitize_identifier("a" * 64, "sqlite")

    def test_accepts_normal_identifier(self):
        from src.core.database.dialect import sanitize_identifier

        assert sanitize_identifier("msg_messages", "sqlite") == "`msg_messages`"
        assert sanitize_identifier("msg_messages", "postgres") == '"msg_messages"'


class TestMigrationVersionPath:
    def test_rejects_traversal_version(self):
        from src.core.migrations.tracker import MigrationTracker

        tracker = MigrationTracker.__new__(MigrationTracker)
        # A traversal/absolute version must never be turned into a module path;
        # the guard returns no metadata rather than executing an outside file.
        assert tracker.get_migration_metadata("../../etc/passwd") == {}
        assert tracker.get_migration_metadata("005;rm -rf") == {}

    def test_accepts_numeric_version(self):
        from src.core.migrations.tracker import MigrationTracker

        tracker = MigrationTracker.__new__(MigrationTracker)
        # Known migration files parse without raising; an unknown numeric one
        # simply yields no metadata.
        assert isinstance(tracker.get_migration_metadata("999"), dict)


class TestClonerIdentifiers:
    def test_content_signature_rejects_hostile_table(self):
        from src.core.migrations.cloner import DataCloner

        cloner = DataCloner.__new__(DataCloner)
        with pytest.raises(HardeningViolation):
            cloner._content_signature(None, "users; DROP TABLE users")

    def test_clone_table_rejects_hostile_table(self):
        from src.core.migrations.cloner import DataCloner

        cloner = DataCloner.__new__(DataCloner)
        with pytest.raises(HardeningViolation):
            cloner.clone_table("bad-name")


class TestFtsQueryBounds:
    def test_build_fts_query_caps_length_and_terms(self):
        from src.core.search.indexer.sqlite_fts import (
            _FTS_MAX_QUERY_CHARS,
            SQLiteFTS5Indexer,
        )

        indexer = SQLiteFTS5Indexer.__new__(SQLiteFTS5Indexer)
        huge = " ".join(f"term{i}" for i in range(500))
        result = indexer._build_fts_query(huge)
        assert len(result) <= _FTS_MAX_QUERY_CHARS + 32
        assert result.count(" OR ") < 16

    def test_build_fts_query_normal(self):
        from src.core.search.indexer.sqlite_fts import SQLiteFTS5Indexer

        indexer = SQLiteFTS5Indexer.__new__(SQLiteFTS5Indexer)
        assert indexer._build_fts_query("hello world") == '"hello"* OR "world"*'
