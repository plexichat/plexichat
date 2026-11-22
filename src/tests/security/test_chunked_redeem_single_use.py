"""Chunked-upload redemption must be single-use and owner-bound.

``complete_session_stream`` is the "redeem" half of the chunked-upload path:
the API route calls it, uploads the assembled temp file to durable storage, and
only *then* cancels the session (which deletes the row and temp file).  Before
the fix, ``complete_session_stream`` was a pure read: it re-read the session and
returned it without changing any state, so two callers could both observe the
COMPLETED session and both redeem the same temp file into durable storage --
reserving storage quota twice for one upload (a double-spend of the reserved
quota) and opening a write-vs-read race on the temp file.

The fix makes redemption an atomic, owner-bound compare-and-set on the session
row: exactly one caller can flip COMPLETED -> REDEEMING, and every other caller
gets ``None``.  These tests ratchet both the behaviour and the SQL shape so the
guard cannot silently regress to a bare read.
"""

import ast
import os
import sqlite3
import time
from pathlib import Path

import pytest

from src.core.media.chunked import ChunkedUploadManager, create_tables

pytestmark = pytest.mark.unit

_SOURCE = Path(__file__).resolve().parents[2] / "core/media/chunked.py"


class _SqliteDB:
    """A real SQLite adapter so the guarded CAS is genuinely evaluated."""

    type = "sqlite"

    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        create_tables(self.conn)

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)

    def fetch_one(self, sql, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def fetch_all(self, sql, params=()):
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def execute_many(self, sql, params=()):
        return self.conn.executemany(sql, params)


@pytest.fixture
def db():
    return _SqliteDB()


@pytest.fixture
def manager(db, tmp_path):
    mgr = ChunkedUploadManager(db)
    mgr._temp_dir = str(tmp_path / "chunked_uploads")
    os.makedirs(mgr._temp_dir, exist_ok=True)
    return mgr


def _completed_session(manager, db, user_id=1):
    """Create a session and fill every chunk so its status is COMPLETED."""
    session = manager.create_session(
        user_id=user_id,
        filename="redeem.bin",
        content_type="application/octet-stream",
        total_size=manager._config["chunk_size"],
    )
    assert session is not None
    result = manager.upload_chunk(session.id, user_id, 0, b"A" * session.total_size)
    assert result.success is True
    assert result.is_complete is True
    assert (
        db.fetch_one("SELECT status FROM media_upload_sessions WHERE id = ?", (session.id,))[
            "status"
        ]
        == "completed"
    )
    return session


def test_second_redemption_is_refused(db, manager):
    """Only the first caller may redeem a completed session."""
    session = _completed_session(manager, db)

    first = manager.complete_session_stream(session.id, 1)
    second = manager.complete_session_stream(session.id, 1)

    assert first is not None
    assert second is None, "a completed session was redeemed twice"


def test_redemption_is_owner_bound(db, manager):
    """A different user must not be able to redeem another user's session."""
    session = _completed_session(manager, db, user_id=1)

    assert manager.complete_session_stream(session.id, 2) is None
    # The legitimate owner can still redeem it afterwards.
    assert manager.complete_session_stream(session.id, 1) is not None


def test_redeemed_session_cannot_be_redeemed_after_retry(db, manager):
    """Repeated attempts (e.g. concurrent racers) never get a second redemption."""
    session = _completed_session(manager, db)

    assert manager.complete_session_stream(session.id, 1) is not None
    for _ in range(3):
        assert manager.complete_session_stream(session.id, 1) is None


def test_chunk_write_cannot_reopen_a_redeemed_session(db, manager):
    """A late/duplicate chunk retry must not reset REDEEMING to COMPLETED.

    ``_record_chunk_progress`` recomputes status on every accepted chunk, so
    without the post-completion write guard a client could redeem, re-upload a
    chunk (flipping the row back to ``completed``) and redeem a second time.
    """
    session = _completed_session(manager, db)
    assert manager.complete_session_stream(session.id, 1) is not None

    late = manager.upload_chunk(session.id, 1, 0, b"B" * session.total_size)
    assert late.success is False
    assert (
        db.fetch_one("SELECT status FROM media_upload_sessions WHERE id = ?", (session.id,))[
            "status"
        ]
        == "redeeming"
    )
    assert manager.complete_session_stream(session.id, 1) is None


def test_cancelled_redeemed_session_still_cleans_temp_file(db, manager):
    """The redeem CAS must not break the route's cleanup-after-upload step."""
    session = _completed_session(manager, db)
    assert manager.complete_session_stream(session.id, 1) is not None

    assert manager.cancel_session(session.id, 1) is True
    assert not os.path.exists(session.temp_path)


def test_source_guards_redemption_with_owner_bound_cas():
    """Ratchet: ``complete_session_stream`` must issue a guarded UPDATE.

    Behavioural tests above can be satisfied by any single-use mechanism, but
    this narrow source guard stops a future refactor from replacing the CAS with
    a plain read (which would silently reopen the double-redeem race).
    """
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "complete_session_stream"
    )
    segment = ast.get_source_segment(_SOURCE.read_text(encoding="utf-8"), fn) or ""

    lowered = segment.lower()
    assert "update media_upload_sessions" in lowered
    assert "status = 'redeeming'" in lowered
    assert "user_id = ?" in lowered
    assert "status = 'completed'" in lowered
    assert "rowcount" in lowered
