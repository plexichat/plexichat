"""Concurrency/bounds regression tests for storage-quota reservations.

The quota check used to be a read-then-write sequence: every worker summed the
live media rows plus pending reservations in Python, then inserted its own
reservation. Under multiple workers that is a TOCTOU race - both can observe a
stale total and both can insert, pushing the user over quota. This suite drives
the check from two threads with independent SQLite connections (a stand-in for
two app workers sharing one database) and asserts the cap still holds.

It also covers the two scaling bounds introduced alongside the atomic check:
expired reservations are pruned globally, and each user is capped on the
number of simultaneous in-flight reservations. Finally it verifies that the
ledger stays scoped to its owner - one user's bytes never count for another and
a global prune never deletes another user's live reservation.
"""

import sqlite3
import threading

import pytest

from src.core.media.exceptions import StorageQuotaExceededError
from src.core.storage_quota import manager as quota_module
from src.core.storage_quota.manager import (
    MAX_ACTIVE_RESERVATIONS_PER_USER,
    MAX_MANAGER_CACHE_ENTRIES,
    MIB,
    StorageQuotaManager,
    get_manager,
)


class _FileDB:
    """SQLite adapter with a thread-local connection per caller.

    Each thread gets its own connection to the same on-disk database, which is
    how separate app workers would see each other's writes. WAL + a busy timeout
    mirror the production SQLite engine settings.
    """

    type = "sqlite"

    def __init__(self, path):
        self.path = path
        self._local = threading.local()

    def _conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                self.path,
                timeout=30,
                isolation_level=None,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    def execute(self, sql, params=()):
        return self._conn().execute(sql, params)

    def fetch_one(self, sql, params=()):
        row = self._conn().execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def fetch_all(self, sql, params=()):
        return [dict(row) for row in self._conn().execute(sql, params).fetchall()]

    def begin_transaction(self):
        self._conn().execute("BEGIN")

    def commit(self):
        self._conn().execute("COMMIT")

    def rollback(self):
        self._conn().execute("ROLLBACK")

    def close(self):
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()


@pytest.fixture
def quota_db(tmp_path):
    db = _FileDB(str(tmp_path / "quota.db"))
    db.execute(
        "CREATE TABLE media_files ("
        "id INTEGER PRIMARY KEY, uploaded_by INTEGER NOT NULL, "
        "size INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0)"
    )
    yield db
    db.close()


def _manager(db, user_id, *, limit_bytes):
    manager = StorageQuotaManager(db, config_override={"default_quota_mb": 1})
    manager._user_tier = lambda _uid: "standard"  # pyright: ignore[reportAttributeAccessIssue]
    manager._tier_limit_bytes = lambda _tier: None  # pyright: ignore[reportAttributeAccessIssue]
    manager.set_override(user_id, limit_bytes=limit_bytes)
    return manager


# ── concurrency ────────────────────────────────────────────────────────────────


def test_concurrent_reservations_never_exceed_cap(quota_db):
    """Two workers racing for the last bytes: at most one may win."""
    user_id = 123456789
    limit = 1000
    _manager(quota_db, user_id, limit_bytes=limit)

    barrier = threading.Barrier(2)
    outcomes = []

    def reserve_once():
        # Each worker gets its own manager, just like its own process would.
        manager = StorageQuotaManager(quota_db, config_override={"default_quota_mb": 1})
        manager._user_tier = lambda _uid: "standard"  # pyright: ignore[reportAttributeAccessIssue]
        manager._tier_limit_bytes = lambda _tier: None  # pyright: ignore[reportAttributeAccessIssue]
        barrier.wait(timeout=15)
        try:
            token = manager.reserve(user_id, 600)
            outcomes.append(("ok", token))
        except StorageQuotaExceededError as exc:
            outcomes.append(("denied", exc))

    threads = [threading.Thread(target=reserve_once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    assert len(outcomes) == 2
    granted = [token for result, token in outcomes if result == "ok"]
    denied = [exc for result, exc in outcomes if result == "denied"]
    # The guard rejects the second request with a quota error, not a raw lock
    # timeout or a silent over-quota reservation.
    assert len(granted) == 1
    assert len(denied) == 1
    assert denied[0].limit_bytes == limit

    status = _manager(quota_db, user_id, limit_bytes=limit).get_status(user_id)
    assert status["pending_bytes"] == 600
    assert status["remaining_bytes"] == 400


def test_guard_uses_atomic_insert_select(quota_db):
    """Enforcement lives in one SQL statement, not a Python check + insert."""
    user_id = 123456789
    manager = _manager(quota_db, user_id, limit_bytes=1000)

    statements = []
    original_execute = quota_db.execute

    def recording_execute(sql, params=()):
        statements.append(sql)
        return original_execute(sql, params)

    quota_db.execute = recording_execute
    try:
        manager.reserve(user_id, 100)
    finally:
        quota_db.execute = original_execute

    inserts = [sql for sql in statements if sql.lstrip().upper().startswith("INSERT")]
    assert len(inserts) == 1
    insert = inserts[0]
    assert "SELECT" in insert.upper()
    assert "COALESCE(SUM(size)" in insert
    assert "COALESCE(SUM(bytes)" in insert


# ── bounds ─────────────────────────────────────────────────────────────────────


def test_expired_reservations_are_pruned_for_every_user(quota_db):
    """A one-time uploader's expired rows must not accumulate forever."""
    manager = _manager(quota_db, 111, limit_bytes=MIB)
    manager._ensure_schema()
    now = quota_module.time.time() * 1000
    for token_suffix, uid in (("a", 111), ("b", 222), ("c", 333)):
        quota_db.execute(
            "INSERT INTO storage_quota_reservations "
            "(token, user_id, bytes, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"expired-{token_suffix}", uid, 100, int(now) - 10000, int(now) - 1),
        )

    manager.reserve(111, 100)

    rows = quota_db.fetch_all("SELECT token FROM storage_quota_reservations")
    assert {row["token"] for row in rows}.isdisjoint(
        {"expired-a", "expired-b", "expired-c"}
    )


def test_per_user_reservation_count_is_bounded(quota_db):
    """Aborted uploads cannot mint unbounded pending rows."""
    user_id = 123456789
    manager = _manager(quota_db, user_id, limit_bytes=MIB)
    manager._user_tier = lambda _uid: "unlimited"  # pyright: ignore[reportAttributeAccessIssue]
    manager._tier_limit_bytes = lambda _tier: -1  # pyright: ignore[reportAttributeAccessIssue]

    tokens = []
    for _ in range(MAX_ACTIVE_RESERVATIONS_PER_USER):
        tokens.append(manager.reserve(user_id, 1))

    with pytest.raises(StorageQuotaExceededError):
        manager.reserve(user_id, 1)

    for token in tokens:
        manager.release(token)


def test_manager_cache_is_bounded_lru(monkeypatch):
    """get_manager must not leak a manager for every DB object ever seen."""
    quota_module._manager_cache.clear()
    try:
        created = [object() for _ in range(MAX_MANAGER_CACHE_ENTRIES + 5)]
        for db in created:
            get_manager(db)
        assert len(quota_module._manager_cache) == MAX_MANAGER_CACHE_ENTRIES
        # The most recently requested DB is retained (LRU, not arbitrary drop).
        assert id(created[-1]) in quota_module._manager_cache
        assert id(created[0]) not in quota_module._manager_cache
    finally:
        quota_module._manager_cache.clear()


# ── no cross-user leak ─────────────────────────────────────────────────────────


def test_one_users_bytes_never_count_for_another(quota_db):
    user_a, user_b = 111111111, 222222222
    manager = _manager(quota_db, user_a, limit_bytes=1000)
    manager.set_override(user_b, limit_bytes=1000)
    quota_db.execute(
        "INSERT INTO media_files (uploaded_by, size, deleted) VALUES (?, ?, 0)",
        (user_b, 900),
    )

    # B is nearly full, but A's allowance is independent.
    assert manager.reserve(user_a, 900)
    with pytest.raises(StorageQuotaExceededError):
        manager.reserve(user_b, 200)


def test_global_prune_keeps_other_users_live_reservations(quota_db):
    """The bound fix must never delete a live reservation owned elsewhere."""
    user_a, user_b = 111111111, 222222222
    manager = _manager(quota_db, user_a, limit_bytes=MIB)
    manager._ensure_schema()
    quota_db.execute(
        "INSERT INTO storage_quota_reservations "
        "(token, user_id, bytes, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("b-live", user_b, 100, 0, int(quota_module.time.time() * 1000) + 60000),
    )

    manager.reserve(user_a, 100)

    row = quota_db.fetch_one(
        "SELECT user_id FROM storage_quota_reservations WHERE token = ?", ("b-live",)
    )
    assert row is not None
    assert row["user_id"] == user_b


# ── limit-snapshot TOCTOU ──────────────────────────────────────────────────────


def test_guard_uses_live_override_limit_not_stale_python_snapshot(
    quota_db, monkeypatch
):
    """An admin quota shrink racing reserve() must win, not a stale snapshot.

    Models the multi-worker interleaving: worker A reads a generous limit in
    Python (``resolve_limit``), worker B commits a smaller override, then A's
    reservation guard executes. Before the fix the guard trusted A's Python
    limit and let the oversized reservation through (a TOCTOU). Enforcement
    must read the committed limit inside the atomic guard instead.
    """
    user_id = 123456789
    manager = _manager(quota_db, user_id, limit_bytes=10_000)

    # Simulate the stale Python snapshot captured before the admin change.
    stale = dict(manager.resolve_limit(user_id))
    stale["limit_bytes"] = 10_000
    stale["unlimited"] = False
    monkeypatch.setattr(manager, "resolve_limit", lambda _uid, **_kw: dict(stale))

    # Worker B commits a smaller cap after A's snapshot was taken.
    quota_db.execute(
        "UPDATE user_storage_quotas SET limit_bytes = ?, unlimited = 0 "
        "WHERE user_id = ?",
        (500, user_id),
    )

    # 1000 fits the stale 10000 snapshot but not the live 500 cap.
    with pytest.raises(StorageQuotaExceededError):
        manager.reserve(user_id, 1000)
    assert manager.pending_bytes(user_id) == 0


def test_guard_honors_live_unlimited_override_not_stale_snapshot(
    quota_db, monkeypatch
):
    """A live unlimited grant racing reserve() is honored by the guard."""
    user_id = 123456789
    manager = _manager(quota_db, user_id, limit_bytes=100)

    stale = dict(manager.resolve_limit(user_id))
    stale["limit_bytes"] = 100
    stale["unlimited"] = False
    monkeypatch.setattr(manager, "resolve_limit", lambda _uid, **_kw: dict(stale))

    quota_db.execute(
        "UPDATE user_storage_quotas SET limit_bytes = NULL, unlimited = 1 "
        "WHERE user_id = ?",
        (user_id,),
    )

    token = manager.reserve(user_id, 5000)
    assert token
    assert manager.pending_bytes(user_id) == 5000
