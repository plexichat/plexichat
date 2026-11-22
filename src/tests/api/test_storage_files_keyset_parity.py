"""Regression guards for the additive keyset path on storage file listing.

``StorageQuotaManager.list_user_files`` keeps its OFFSET contract untouched and
gains an opt-in ``before_id`` cursor. These tests pin three properties:

* result parity -- a keyset page at the last id of an offset prefix is exactly
  the page the old OFFSET query returned, with the same response keys;
* bounded cost -- the keyset branch is one statement with ``id < ?`` and a
  bound ``LIMIT ?`` and no OFFSET walk, regardless of how deep the page is;
* authorization -- the ``uploaded_by`` scope is on both branches, at the
  manager and at the HTTP handler, and the cursor itself is validated before
  it ever reaches SQL.
"""

import time
from typing import Any

import pytest

import src.api as api
from src.core.auth.models import AccountType, TokenInfo
from src.core.security.hardening import HardeningViolation
from src.core.storage_quota.manager import StorageQuotaManager

pytestmark = [pytest.mark.unit]

_USER = 42
_OTHER = 99


class _CountingDB:
    """Delegate to the real DB while counting reads and capturing SQL."""

    def __init__(self, db):
        object.__setattr__(self, "db", db)
        object.__setattr__(self, "reads", 0)
        object.__setattr__(self, "sql", [])
        object.__setattr__(self, "params", [])

    def __getattr__(self, name):
        attr = getattr(self.db, name)

        def wrapped(*args, **kwargs):
            if name in ("fetch_all", "fetch_one"):
                object.__setattr__(self, "reads", self.reads + 1)
                object.__setattr__(self, "sql", self.sql + [args[0] if args else ""])
                params = kwargs.get("params", args[1] if len(args) > 1 else ())
                object.__setattr__(self, "params", self.params + [params])
            return attr(*args, **kwargs)

        return wrapped


def _user(user_id: int = _USER) -> TokenInfo:
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=user_id,
        user_id=user_id,
        session_id=None,
        permissions={},
        rate_limit_tier="default",
        expires_at=None,
        username="tester",
        account_type=AccountType.USER,
    )


def _seed(db, per_user: int = 40) -> None:
    """Insert interleaved live rows for two users with deterministic ids."""
    db.execute("DELETE FROM media_files")
    now = int(time.time() * 1000)
    for i in range(per_user):
        for uid in (_USER, _OTHER):
            db.execute(
                "INSERT INTO media_files (uploaded_by, filename, original_filename, "
                "content_type, media_type, storage_backend, size, storage_path, "
                "uploaded_at, deleted) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    uid,
                    f"f{i}.bin",
                    f"f{i}.bin",
                    "application/octet-stream",
                    "file",
                    "local",
                    1024,
                    f"uploads/{uid}/f{i}.bin",
                    now,
                    0,
                ),
            )


def _manager(db) -> StorageQuotaManager:
    return StorageQuotaManager(db, config_override={"default_quota_mb": 4})


def test_keyset_page_matches_offset_page_with_one_bounded_query(db):
    _seed(db)
    mgr = _manager(db)

    # Cut the cursor at the 8th row of the caller's ordered page.
    prefix = mgr.list_user_files(_USER, limit=8)
    cursor = prefix["files"][-1]["id"]
    assert isinstance(cursor, str)
    expected = mgr.list_user_files(_USER, limit=5, offset=8)

    counting = _CountingDB(db)
    got = _manager(counting).list_user_files(_USER, limit=5, before_id=cursor)

    # Result parity: the same files in the same order and the same response
    # shape. The paging metadata differs by design -- a keyset page has no
    # offset to report, so it echoes 0 rather than the implied position.
    assert got["files"] == expected["files"]
    assert list(got) == ["files", "limit", "offset"]
    assert got["limit"] == expected["limit"] == 5
    assert got["offset"] == 0

    # Bounded cost: one read, no OFFSET walk, cursor + limit both bound params.
    assert counting.reads == 1, counting.sql
    sql = counting.sql[-1]
    assert "id < ?" in sql
    assert "LIMIT ?" in sql
    assert "OFFSET" not in sql
    params = counting.params[-1]
    assert params[0] == _USER
    assert int(cursor) in params
    assert params[-1] == 5


def test_keyset_page_matches_offset_page_with_path_prefix(db):
    _seed(db)
    mgr = _manager(db)
    path = "uploads/42/f"

    prefix = mgr.list_user_files(_USER, limit=6, path_prefix=path)
    cursor = prefix["files"][-1]["id"]
    expected = mgr.list_user_files(_USER, limit=4, offset=6, path_prefix=path)

    got = _manager(db).list_user_files(
        _USER, limit=4, before_id=cursor, path_prefix=path
    )
    assert got["files"] == expected["files"]
    assert got["limit"] == expected["limit"] == 4


def test_keyset_keeps_uploader_scope_at_manager(db):
    _seed(db)
    mgr = _manager(db)

    # A cursor taken from the *other* user's rows cannot widen the scope:
    # ``uploaded_by = ?`` stays on the keyset branch.
    other = mgr.list_user_files(_OTHER, limit=1)
    foreign_cursor = other["files"][0]["id"]

    counting = _CountingDB(db)
    page = _manager(counting).list_user_files(_USER, limit=50, before_id=foreign_cursor)
    assert "uploaded_by = ?" in counting.sql[-1]
    assert counting.params[-1][0] == _USER
    assert page["files"]
    assert all(f["storage_path"].startswith(f"uploads/{_USER}/") for f in page["files"])
    assert all(f["id"] != foreign_cursor for f in page["files"])


@pytest.mark.asyncio
async def test_keyset_route_rejects_foreign_cursor_without_leak(db, monkeypatch):
    _seed(db)
    import src.core.storage_quota.manager as sq
    from src.api.routes import storage as route

    monkeypatch.setattr(api, "_db", db)
    monkeypatch.setattr(api, "get_db", lambda: db)
    sq._manager_cache.clear()
    other = _manager(db).list_user_files(_OTHER, limit=1)
    foreign_cursor = other["files"][0]["id"]

    result = await route.list_my_storage_files(
        limit=50, before_id=foreign_cursor, current_user=_user(_USER)
    )
    assert all(
        f["storage_path"].startswith(f"uploads/{_USER}/") for f in result["files"]
    )
    assert all(f["id"] != foreign_cursor for f in result["files"])


@pytest.mark.parametrize(
    "bad",
    [
        0,
        -1,
        "abc",
        "1_0",
        "٤٢",
        2**63,
        "9" * 5000,
        float("nan"),
        True,
    ],
)
def test_keyset_rejects_invalid_cursor_before_touching_db(db, bad: Any):
    counting = _CountingDB(db)
    with pytest.raises(HardeningViolation):
        _manager(counting).list_user_files(_USER, before_id=bad)
    assert counting.reads == 0


def test_keyset_rejects_mixing_with_nonzero_offset(db):
    _seed(db)
    cursor = _manager(db).list_user_files(_USER, limit=1)["files"][0]["id"]
    counting = _CountingDB(db)
    with pytest.raises(HardeningViolation):
        _manager(counting).list_user_files(_USER, limit=5, offset=3, before_id=cursor)
    assert counting.reads == 0


def test_existing_offset_path_is_unchanged(db):
    """The original statement and response shape survive the additive change."""
    _seed(db)
    counting = _CountingDB(db)
    page = _manager(counting).list_user_files(_USER, limit=5, offset=7)

    assert counting.reads == 1
    assert "LIMIT ? OFFSET ?" in counting.sql[-1]
    assert "id < ?" not in counting.sql[-1]
    assert counting.params[-1] == (_USER, 5, 7)
    assert list(page) == ["files", "limit", "offset"]
    assert page["limit"] == 5 and page["offset"] == 7
