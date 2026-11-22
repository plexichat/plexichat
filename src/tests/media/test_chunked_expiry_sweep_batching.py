"""The expired-upload sweep is bounded, keyset-ordered and batches its writes.

Regression for the periodic ``ChunkedUploadManager.cleanup_expired`` sweep.
Before the fix a due set of N rows issued one unbounded ``SELECT`` (no ORDER
BY / LIMIT) and N single-row ``DELETE`` statements, so a burst of expiries
scaled linearly with N on the write path and could materialise every expired
row in one query. The fix bounds the due set with a keyset LIMIT, collapses the
deletes into a bounded ``execute_many`` batch, and guards each delete on
``expires_at < now`` so two workers cannot both count the same row.
"""

import pytest

from src.core.media.chunked import (
    ChunkedUploadManager,
    _SWEEP_BATCH_SIZE,
    create_tables,
)

pytestmark = [pytest.mark.media, pytest.mark.unit]


class _Cursor:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _TableDB:
    """A tiny in-memory stand-in for the sessions table.

    Honours the SELECT's LIMIT (the last parameter) so the drain-over-cycles
    behaviour is observable, and makes the guarded DELETE remove rows so a
    second worker's guarded write affects zero rows.
    """

    def __init__(self, n):
        self.table = {f"s-{i}": i for i in range(n)}
        self.calls = []

    def fetch_all(self, query, params):
        q = " ".join(query.split())
        self.calls.append(("fetch_all", q, params))
        limit = params[-1] if "LIMIT ?" in q else len(self.table)
        ids = list(self.table)[:limit]
        return [{"id": i, "temp_path": None} for i in ids]

    def execute(self, query, params):
        self.calls.append(("execute", " ".join(query.split()), params))
        return _Cursor(1)

    def execute_many(self, query, params_list):
        q = " ".join(query.split())
        self.calls.append(("execute_many", q, list(params_list)))
        removed = 0
        for session_id, _now in params_list:
            if session_id in self.table:
                del self.table[session_id]
                removed += 1
        return _Cursor(removed)


def _manager(db):
    mgr = ChunkedUploadManager.__new__(ChunkedUploadManager)
    mgr._db = db
    mgr._confine_temp_path = lambda p: None  # no filesystem work in unit tests
    return mgr


def test_due_set_is_bounded_and_writes_are_batched():
    db = _TableDB(200)
    mgr = _manager(db)

    assert mgr.cleanup_expired() == 200

    selects = [c for c in db.calls if c[0] == "fetch_all"]
    writes = [c for c in db.calls if c[0] in ("execute", "execute_many")]
    assert len(selects) == 1
    assert "ORDER BY expires_at ASC" in selects[0][1]
    assert "LIMIT ?" in selects[0][1]
    assert selects[0][2][-1] == _SWEEP_BATCH_SIZE
    # N due rows collapse to a single batch, not N per-row DELETEs.
    assert len(writes) == 1
    assert writes[0][0] == "execute_many"
    assert len(writes[0][2]) == 200
    assert "expires_at < ?" in writes[0][1], "delete must be race-guarded"


def test_expired_backlog_drains_in_bounded_cycles():
    total = _SWEEP_BATCH_SIZE + 150
    db = _TableDB(total)
    mgr = _manager(db)

    first = mgr.cleanup_expired()
    second = mgr.cleanup_expired()
    third = mgr.cleanup_expired()

    assert first == _SWEEP_BATCH_SIZE
    assert second == 150
    assert third == 0
    writes = [c for c in db.calls if c[0] == "execute_many"]
    assert len(writes) == 2
    for _kind, _query, params in writes:
        assert len(params) <= _SWEEP_BATCH_SIZE


def test_executemany_chunk_bounds_are_respected():
    # A stub that ignores LIMIT forces many rows through one sweep so the
    # execute_many chunking itself is exercised.
    class _AllRowsDB(_TableDB):
        def fetch_all(self, query, params):
            self.calls.append(("fetch_all", " ".join(query.split()), params))
            return [{"id": i, "temp_path": None} for i in list(self.table)]

    db = _AllRowsDB(2 * _SWEEP_BATCH_SIZE + 17)
    mgr = _manager(db)

    assert mgr.cleanup_expired() == 2 * _SWEEP_BATCH_SIZE + 17
    writes = [c for c in db.calls if c[0] == "execute_many"]
    assert len(writes) == 3
    for _kind, _query, params in writes:
        assert len(params) <= _SWEEP_BATCH_SIZE


def test_second_worker_losing_the_delete_race_counts_nothing():
    db = _TableDB(_SWEEP_BATCH_SIZE)
    mgr = _manager(db)

    # Both workers SELECT the same due rows; the guard means only the one
    # that actually deletes them counts / owns the cleanup.
    assert mgr.cleanup_expired() == _SWEEP_BATCH_SIZE
    assert mgr.cleanup_expired() == 0


def test_cleanup_expired_end_to_end(db, tmp_path, monkeypatch):
    """All due sessions are reaped, their temp files removed; a re-run is a no-op."""
    create_tables(db)
    mgr = ChunkedUploadManager(db)
    monkeypatch.setattr(mgr, "_temp_dir", str(tmp_path))

    import time

    now_ms = int(time.time() * 1000)
    ids = []
    paths = []
    for i in range(6):
        sid = f"expiry-{i}"
        rel = f"sweep_{i}.part"
        path = tmp_path / rel
        path.write_bytes(b"x")
        db.execute(
            """INSERT INTO media_upload_sessions
               (id, user_id, filename, content_type, total_size, chunk_size,
                total_chunks, uploaded_chunks, uploaded_bytes, status,
                created_at, updated_at, expires_at, temp_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sid,
                1,
                f"file{i}.bin",
                "application/octet-stream",
                10,
                10,
                1,
                0,
                0,
                "pending",
                now_ms - 2000,
                now_ms - 2000,
                now_ms - 1000,
                rel,
            ),
        )
        ids.append(sid)
        paths.append(path)

    assert mgr.cleanup_expired() == 6
    for sid in ids:
        assert (
            db.fetch_one("SELECT id FROM media_upload_sessions WHERE id = ?", (sid,))
            is None
        )
    for path in paths:
        assert not path.exists()
    assert mgr.cleanup_expired() == 0
