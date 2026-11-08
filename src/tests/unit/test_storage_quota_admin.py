"""Route-level tests for admin storage-quota controls."""

import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.api.routes.admin.users import management
from src.api.routes.admin.users.management import ManagementMixin
from src.core.storage_quota.manager import MIB, StorageQuotaManager


class _SqliteDB:
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


class _Router(ManagementMixin):
    def __init__(self, db):
        self._db = db

    def _get_db(self):
        return self._db

    def _get_auth(self):
        raise AssertionError("storage routes must not need the auth manager")

    def _parse_user_id(self, user_id):
        return int(user_id)


def _request():
    return SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))


def _manager(db):
    manager = StorageQuotaManager(db, config_override={"default_quota_mb": 1})
    manager._user_tier = lambda _user_id: "standard"
    manager._tier_limit_bytes = lambda _tier: None
    return manager


def _call(db, method: str, body=None):
    router = _Router(db)
    args = (_request(), "42") if body is None else (_request(), "42", body)
    with (
        patch.object(management, "check_host_restriction", lambda request: None),
        patch.object(management, "require_admin_permission", return_value=11),
        patch("src.core.storage_quota.get_manager", return_value=_manager(db)),
    ):
        return asyncio.run(getattr(router, method)(*args))


@pytest.fixture
def quota_db():
    db = _SqliteDB()
    yield db
    db.conn.close()


def test_get_storage_returns_usage_and_override(quota_db):
    manager = _manager(quota_db)
    manager._ensure_schema()
    quota_db.execute(
        "INSERT INTO media_files (uploaded_by, size) VALUES (?, ?)",
        (42, 128 * 1024),
    )
    manager.set_override(42, limit_bytes=2 * MIB, note="support credit", updated_by=11)

    body = _call(quota_db, "get_user_storage")

    assert body["user_id"] == "42"
    assert body["used_bytes"] == 128 * 1024
    assert body["limit_bytes"] == 2 * MIB
    assert body["source"] == "override"
    assert body["override"]["note"] == "support credit"
    assert body["override"]["updated_by"] == 11


def test_set_storage_override_persists_and_returns_status(quota_db):
    body = _call(quota_db, "update_user_storage", {"limit_bytes": 3 * MIB})

    assert body["success"] is True
    assert body["user_id"] == "42"
    assert body["limit_bytes"] == 3 * MIB
    assert body["source"] == "override"
    assert body["override"]["updated_by"] == 11


def test_set_unlimited_storage_override(quota_db):
    body = _call(
        quota_db,
        "update_user_storage",
        {"unlimited": True, "note": "trusted uploader"},
    )

    assert body["unlimited"] is True
    assert body["limit_bytes"] is None
    assert body["override"]["unlimited"] is True
    assert body["override"]["limit_bytes"] is None


def test_clear_storage_override_falls_back_to_default(quota_db):
    _call(quota_db, "update_user_storage", {"limit_bytes": 3 * MIB})

    body = _call(quota_db, "update_user_storage", {"clear": True})

    assert body["source"] == "default"
    assert body["override"] is None
    assert body["limit_bytes"] == MIB


@pytest.mark.parametrize("bad_value", [-1, "not-a-number", None, {}, 1.5])
def test_invalid_storage_limits_are_rejected(quota_db, bad_value):
    with pytest.raises(HTTPException) as excinfo:
        _call(quota_db, "update_user_storage", {"limit_bytes": bad_value})

    assert excinfo.value.status_code == 400
    assert _manager(quota_db).get_override(42) is None
