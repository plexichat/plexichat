"""Focused tests for per-user storage-quota policy and reservations."""

import sqlite3

import pytest

from src.core.media.exceptions import StorageQuotaExceededError
from src.core.storage_quota.manager import MIB, StorageQuotaManager


class _SqliteDB:
    """Small synchronous DB adapter matching the quota manager's interface."""

    type = "sqlite"

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self._in_transaction = False
        self.execute(
            "CREATE TABLE media_files ("
            "id INTEGER PRIMARY KEY, uploaded_by INTEGER NOT NULL, "
            "size INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0)"
        )

    def execute(self, sql, params=()):
        cursor = self.conn.execute(sql, params)
        if not self._in_transaction:
            self.conn.commit()
        return cursor

    def fetch_one(self, sql, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def fetch_all(self, sql, params=()):
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def begin_transaction(self):
        self.conn.execute("BEGIN")
        self._in_transaction = True

    def commit(self):
        self.conn.commit()
        self._in_transaction = False

    def rollback(self):
        self.conn.rollback()
        self._in_transaction = False

    def close(self):
        self.conn.close()


@pytest.fixture
def quota_db():
    db = _SqliteDB()
    yield db
    db.close()


def _manager(quota_db, *, default_mb=1, tier_bytes=None, tier="standard"):
    manager = StorageQuotaManager(
        quota_db, config_override={"default_quota_mb": default_mb}
    )
    manager._user_tier = lambda _user_id: tier
    manager._tier_limit_bytes = lambda _tier: tier_bytes
    return manager


def _add_media(quota_db, user_id, size, *, deleted=0):
    quota_db.execute(
        "INSERT INTO media_files (uploaded_by, size, deleted) VALUES (?, ?, ?)",
        (user_id, size, deleted),
    )


def test_default_limit_and_usage(quota_db):
    manager = _manager(quota_db)
    _add_media(quota_db, 1, 256 * 1024)
    _add_media(quota_db, 1, 256 * 1024)
    _add_media(quota_db, 1, 8 * MIB, deleted=1)
    _add_media(quota_db, 2, 4 * MIB)

    status = manager.get_status(1)

    assert status["limit_bytes"] == MIB
    assert status["used_bytes"] == 512 * 1024
    assert status["remaining_bytes"] == 512 * 1024
    assert status["file_count"] == 2
    assert status["source"] == "default"
    assert status["tier"] == "standard"
    assert status["unlimited"] is False


def test_reservation_counts_pending_bytes_and_releases(quota_db):
    manager = _manager(quota_db)
    _add_media(quota_db, 1, 512 * 1024)

    token = manager.reserve(1, 512 * 1024)
    assert token

    status = manager.get_status(1)
    assert status["pending_bytes"] == 512 * 1024
    assert status["remaining_bytes"] == 0

    with pytest.raises(StorageQuotaExceededError) as excinfo:
        manager.reserve(1, 1)
    assert excinfo.value.limit_bytes == MIB
    assert excinfo.value.used_bytes == MIB
    assert excinfo.value.requested_bytes == 1

    manager.release(token)
    assert manager.get_status(1)["pending_bytes"] == 0

    # Releasing twice is safe.
    manager.release(token)


def test_tier_limit_takes_precedence_over_default(quota_db):
    manager = _manager(quota_db, default_mb=1, tier_bytes=2 * MIB)

    status = manager.get_status(1)

    assert status["limit_bytes"] == 2 * MIB
    assert status["source"] == "tier"
    assert status["tier_limit_bytes"] == 2 * MIB
    assert status["default_limit_bytes"] == MIB


def test_tier_unlimited(quota_db):
    manager = _manager(quota_db, tier_bytes=-1)

    status = manager.get_status(1)

    assert status["unlimited"] is True
    assert status["limit_bytes"] is None
    assert status["remaining_bytes"] is None
    assert status["source"] == "tier"
    assert manager.reserve(1, 100 * MIB)


def test_admin_override_and_clear(quota_db):
    manager = _manager(quota_db)
    _add_media(quota_db, 1, 256 * 1024)

    status = manager.set_override(1, limit_bytes=2 * MIB, updated_by=99)

    assert status["limit_bytes"] == 2 * MIB
    assert status["source"] == "override"
    override = manager.get_override(1)
    assert override is not None
    assert override["updated_by"] == 99

    status = manager.set_override(1, unlimited=True, updated_by=99)
    assert status["unlimited"] is True
    assert status["limit_bytes"] is None
    assert manager.reserve(1, 10 * MIB)

    status = manager.clear_override(1)
    assert status["source"] == "default"
    assert status["limit_bytes"] == MIB
    assert manager.get_override(1) is None


def test_zero_byte_override_blocks_every_upload(quota_db):
    manager = _manager(quota_db)
    manager.set_override(1, limit_bytes=0)

    status = manager.get_status(1)
    assert status["limit_bytes"] == 0
    assert status["percent_used"] == 100.0

    with pytest.raises(StorageQuotaExceededError):
        manager.reserve(1, 1)


def test_expired_reservations_do_not_count(quota_db):
    manager = _manager(quota_db)
    manager._ensure_schema()
    quota_db.execute(
        "INSERT INTO storage_quota_reservations "
        "(token, user_id, bytes, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
        ("expired", 1, MIB, 0, 1),
    )

    assert manager.get_status(1)["pending_bytes"] == 0
    token = manager.reserve(1, MIB)
    assert token
    manager.release(token)


def test_zero_byte_reservation_is_a_noop(quota_db):
    manager = _manager(quota_db)

    assert manager.reserve(1, 0) is None
    assert manager.get_status(1)["pending_bytes"] == 0
