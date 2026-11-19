"""Regression: account erasure must purge storage-quota rows.

``user_storage_quotas.user_id`` and ``storage_quota_reservations.user_id``
have no foreign key to ``auth_users``. ``erase_user_residue`` used to ignore
both, so a full account purge left the override row (including its free-text,
PII-bearing admin note) and any in-flight reservations queryable under a user
id that no longer existed. This suite verifies the shared erasure helper and
the legacy orphan sweep now remove that residue without touching live users.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.core import erasure
from src.core.erasure import (
    erase_user_residue,
    purge_user_storage_quota,
    sweep_orphans,
)

UID = 111111111
OTHER = 222222222


class _QuotaDB:
    """Thin sqlite wrapper providing the fetch_all helper the erasure module uses."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        return self._conn.execute(sql, params)

    def fetch_all(self, sql, params=()):
        return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    def fetch_one(self, sql, params=()):
        row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def close(self):
        self._conn.close()


@pytest.fixture
def quota_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db = _QuotaDB(conn)
    db.execute(
        "CREATE TABLE auth_users (id INTEGER PRIMARY KEY, username TEXT)"
    )
    # sweep_orphans queries media_files for orphan uploaders even though the
    # media purge itself is monkeypatched in these tests.
    db.execute(
        "CREATE TABLE media_files ("
        "id INTEGER PRIMARY KEY, uploaded_by INTEGER, size INTEGER, deleted INTEGER)"
    )
    db.execute(
        "CREATE TABLE user_storage_quotas ("
        "user_id INTEGER PRIMARY KEY, limit_bytes INTEGER, unlimited INTEGER, "
        "note TEXT, updated_by INTEGER, updated_at INTEGER)"
    )
    db.execute(
        "CREATE TABLE storage_quota_reservations ("
        "token TEXT PRIMARY KEY, user_id INTEGER, bytes INTEGER, "
        "created_at INTEGER, expires_at INTEGER)"
    )
    for uid in (UID, OTHER):
        db.execute("INSERT INTO auth_users (id, username) VALUES (?, ?)", (uid, str(uid)))
        db.execute(
            "INSERT INTO user_storage_quotas "
            "(user_id, limit_bytes, unlimited, note, updated_by, updated_at) "
            "VALUES (?, ?, 0, ?, ?, ?)",
            (uid, 1024, f"private note about user {uid}", 999, 0),
        )
        db.execute(
            "INSERT INTO storage_quota_reservations "
            "(token, user_id, bytes, created_at, expires_at) "
            "VALUES (?, ?, ?, 0, 0)",
            (f"token-{uid}", uid, 512),
        )
    yield db
    db.close()


def _quota_rows(db, uid):
    override = db.execute(
        "SELECT note FROM user_storage_quotas WHERE user_id = ?", (uid,)
    ).fetchone()
    reservations = db.execute(
        "SELECT token FROM storage_quota_reservations WHERE user_id = ?", (uid,)
    ).fetchall()
    return override, reservations


def test_purge_user_storage_quota_removes_only_target_user(quota_db):
    deleted = purge_user_storage_quota(quota_db, UID)

    # One override + one reservation for the target user.
    assert deleted == 2
    assert _quota_rows(quota_db, UID) == (None, [])
    # A different user's quota data must survive untouched.
    override, reservations = _quota_rows(quota_db, OTHER)
    assert override is not None
    assert override["note"] == f"private note about user {OTHER}"
    assert [row["token"] for row in reservations] == [f"token-{OTHER}"]


def test_purge_user_storage_quota_tolerates_missing_tables():
    """An install predating storage quotas must not abort erasure."""
    db = sqlite3.connect(":memory:")
    try:
        assert purge_user_storage_quota(db, UID) == 0
    finally:
        db.close()


def test_erase_user_residue_includes_quota_purge(quota_db, monkeypatch):
    # Isolate the orchestration: only the quota purge runs for real.
    monkeypatch.setattr(erasure, "purge_user_message_residue", lambda *a, **k: 0)
    monkeypatch.setattr(erasure, "purge_user_notifications", lambda *a, **k: 0)
    monkeypatch.setattr(erasure, "purge_user_personal_rows", lambda *a, **k: 0)
    monkeypatch.setattr(erasure, "purge_user_credentials", lambda *a, **k: 0)
    monkeypatch.setattr(erasure, "purge_owned_servers", lambda *a, **k: [])
    monkeypatch.setattr(erasure, "purge_user_conversations", lambda *a, **k: [])
    monkeypatch.setattr(erasure, "purge_user_media", lambda *a, **k: [])

    result = erase_user_residue(quota_db, UID)

    assert result["storage_quota"] == 2
    assert _quota_rows(quota_db, UID) == (None, [])
    assert _quota_rows(quota_db, OTHER)[0] is not None


def test_sweep_orphans_reaps_dead_owner_quota_rows(quota_db, monkeypatch):
    # Simulate a legacy purge: the account row is gone but its quota residue
    # (with the PII note) was left behind by an older build.
    quota_db.execute("DELETE FROM auth_users WHERE id = ?", (UID,))

    monkeypatch.setattr(erasure, "purge_user_media", lambda *a, **k: [])
    monkeypatch.setattr(erasure, "purge_user_credentials", lambda *a, **k: 0)
    monkeypatch.setattr(erasure, "sweep_orphaned_servers", lambda *a, **k: [])
    monkeypatch.setattr(erasure, "purge_orphaned_conversations", lambda *a, **k: [])

    result = sweep_orphans(quota_db)

    assert result["storage_quota"] == [UID]
    assert _quota_rows(quota_db, UID) == (None, [])
    # The still-live account keeps its quota override.
    assert _quota_rows(quota_db, OTHER)[0] is not None
