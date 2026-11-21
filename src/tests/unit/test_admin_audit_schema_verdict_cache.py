"""Regression: admin audit writes do not re-introspect the schema every time.

``AdminLogger._log_to_database`` used to call ``db.column_exists(...)`` on every
single audit write just to choose between two INSERT shapes. On SQLite that is
a ``sqlite_master`` lookup plus ``PRAGMA table_info`` (two round-trips); on
PostgreSQL an ``information_schema`` query. The verdict is cached on the
singleton logger with a hard entry cap and a short TTL. These tests pin that:

* one introspection serves many writes (the perf fix);
* the INSERT shape still follows the verdict (both branches);
* a failed introspection is not cached and stays fail-closed;
* the memo is bounded and short-TTL, and can be invalidated after a migration.
"""

from __future__ import annotations

import pytest

import src.core.admin.logging as admin_logging
from src.core.admin.logging import AdminLogEntry, AdminLogger


class FakeDB:
    def __init__(self, exists: bool = True) -> None:
        self.exists = exists
        self.introspections = 0
        self.error: Exception | None = None
        self.inserts: list[tuple[str, tuple]] = []

    def column_exists(self, table, column):
        self.introspections += 1
        if self.error is not None:
            raise self.error
        return self.exists

    def execute(self, sql, params=()):
        self.inserts.append((sql, tuple(params)))
        return None


@pytest.fixture
def logger_and_db():
    logger = AdminLogger(file_logger=None)
    return logger


def _entry():
    return AdminLogEntry(
        admin_id=7,
        action="config_update",
        target_type="user",
        target_id=42,
        status="success",
    )


def test_schema_verdict_introspected_once_across_many_writes(logger_and_db):
    logger = logger_and_db
    db = FakeDB(exists=True)
    # Bypass the lazy summary-cache import's side effects in this unit test.
    for _ in range(50):
        assert logger.log_action(db, _entry(), log_to_file=False) is True
    assert db.introspections == 1
    assert len(db.inserts) == 50
    # Extended schema -> the 11-column INSERT that carries target_user_id.
    assert "target_user_id" in db.inserts[0][0]


def test_reduced_schema_uses_nine_column_insert(logger_and_db):
    logger = logger_and_db
    db = FakeDB(exists=False)
    logger.log_action(db, _entry(), log_to_file=False)
    assert db.introspections == 1
    assert "target_user_id" not in db.inserts[0][0]
    assert len(db.inserts[0][1]) == 9


def test_failed_introspection_is_not_cached_and_fails_closed(logger_and_db):
    logger = logger_and_db
    db = FakeDB(exists=True)
    db.error = RuntimeError("schema unavailable")
    # Failure keeps the historical "extended schema" branch and is not cached.
    logger.log_action(db, _entry(), log_to_file=False)
    assert db.introspections == 1
    assert "target_user_id" in db.inserts[0][0]
    assert logger._schema_verdicts == {}
    # The next write retries the check instead of pinning the failure.
    db.error = None
    logger.log_action(db, _entry(), log_to_file=False)
    assert db.introspections == 2


def test_verdict_cache_is_bounded(logger_and_db, monkeypatch):
    logger = logger_and_db
    monkeypatch.setattr(admin_logging, "_SCHEMA_VERDICT_MAX_ENTRIES", 4)
    db = FakeDB()
    for i in range(50):
        logger._column_exists_cached(db, f"table_{i}", "col")
    assert len(logger._schema_verdicts) <= 4
    # Still live: the last verdict is present and cheap.
    before = db.introspections
    key = ("table_49", "col")
    assert logger._column_exists_cached(db, *key) is True
    assert db.introspections == before


def test_ttl_expiry_refreshes_verdict(logger_and_db, monkeypatch):
    logger = logger_and_db
    db = FakeDB(exists=True)
    assert logger._column_exists_cached(db, "admin_audit_log", "target_user_id") is True
    assert db.introspections == 1
    # Force the cached entry to be already expired.
    key = ("admin_audit_log", "target_user_id")
    logger._schema_verdicts[key] = (0.0, True)
    db.exists = False
    assert logger._column_exists_cached(db, *key) is False
    assert db.introspections == 2


def test_clear_schema_cache_forces_reintrospection(logger_and_db):
    logger = logger_and_db
    db = FakeDB(exists=True)
    logger.log_action(db, _entry(), log_to_file=False)
    assert db.introspections == 1
    logger.clear_schema_cache()
    assert logger._schema_verdicts == {}
    logger.log_action(db, _entry(), log_to_file=False)
    assert db.introspections == 2
