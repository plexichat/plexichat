"""TOCTOU regression for the per-user chunked-upload session cap.

``ChunkedUploadManager.create_session`` used to run a ``SELECT COUNT(*)`` of a
user's active upload sessions and *then* issue a separate bare ``INSERT``. Two
concurrent callers could both read a below-cap count before either committed and
both insert, pushing a single user past ``max_active_sessions_per_user`` and
letting them hoard sparse temp files (a per-user cap bypass of the same class
already fixed for emoji/sticker/role/app quotas).

The cap is now enforced by a single atomic guarded INSERT (plus a PostgreSQL
advisory lock). This module proves the authoritative guard still rejects the
overflow even when the advisory count read is stale (the losing-racer
interleaving), that a rejected create leaves no orphaned temp file, and ratchets
the source so the guarded INSERT cannot silently regress to a bare INSERT.
"""

import ast
import os
import sqlite3
import time
from pathlib import Path

import pytest

from src.core.media.chunked import ChunkedUploadManager

pytestmark = pytest.mark.unit

_SOURCE = Path(__file__).resolve().parents[2] / "core/media/chunked.py"
_COUNT_SQL = "SELECT COUNT(*) AS cnt FROM media_upload_sessions"
_CAP = 3


class _SqliteDB:
    """Real SQLite adapter so the guarded INSERT is genuinely evaluated.

    ``type = 'sqlite'`` keeps the production SQLite branch (no advisory lock).
    A single connection is sufficient for the stale-count probe: the guard must
    read the table state directly in SQL, not through the monkeypatched
    ``fetch_one`` used to model the stale advisory read.
    """

    type = "sqlite"

    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """CREATE TABLE media_upload_sessions (
                   id TEXT PRIMARY KEY,
                   user_id INTEGER NOT NULL,
                   filename TEXT NOT NULL,
                   content_type TEXT NOT NULL,
                   total_size INTEGER NOT NULL,
                   chunk_size INTEGER NOT NULL,
                   total_chunks INTEGER NOT NULL,
                   uploaded_chunks INTEGER NOT NULL DEFAULT 0,
                   uploaded_bytes INTEGER NOT NULL DEFAULT 0,
                   status TEXT NOT NULL DEFAULT 'pending',
                   created_at INTEGER NOT NULL,
                   updated_at INTEGER NOT NULL,
                   expires_at INTEGER NOT NULL,
                   temp_path TEXT,
                   checksum TEXT,
                   chunk_checksums TEXT,
                   received_chunks TEXT
               )"""
        )

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)

    def fetch_one(self, sql, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def fetch_all(self, sql, params=()):
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]


@pytest.fixture
def chunked_db():
    return _SqliteDB()


def _manager(chunked_db, tmp_path):
    mgr = ChunkedUploadManager(chunked_db)
    mgr._temp_dir = str(tmp_path / "chunked_uploads")
    os.makedirs(mgr._temp_dir, exist_ok=True)
    mgr._config["max_active_sessions_per_user"] = _CAP
    return mgr


def _seed_active(chunked_db, user_id, count):
    now = int(time.time() * 1000)
    for i in range(count):
        chunked_db.execute(
            """INSERT INTO media_upload_sessions
               (id, user_id, filename, content_type, total_size, chunk_size,
                total_chunks, status, created_at, updated_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (
                f"seed-{i}",
                user_id,
                f"seed_{i}.bin",
                "application/octet-stream",
                1024,
                1024,
                1,
                now,
                now,
                now + 3_600_000,
            ),
        )


def _active_count(chunked_db, user_id):
    return chunked_db.fetch_one(
        "SELECT COUNT(*) AS n FROM media_upload_sessions WHERE user_id = ?",
        (user_id,),
    )["n"]


def test_stale_count_probe_cannot_exceed_session_cap(chunked_db, tmp_path, monkeypatch):
    """A stale below-cap advisory read must not push a user past the cap.

    The table is filled to ``_CAP`` while ``fetch_one`` is forced to report a
    stale count of 0 (the losing-racer interleaving). Only an authoritative
    atomic guard can refuse the overflow row. On the pre-fix code the bare
    INSERT goes straight through and the cap is exceeded -- this assertion is
    the non-vacuous proof of the TOCTOU.
    """
    user_id = 424242
    _seed_active(chunked_db, user_id, _CAP)
    assert _active_count(chunked_db, user_id) == _CAP

    real_fetch_one = chunked_db.fetch_one

    def stale_count(sql, params=()):
        if _COUNT_SQL in sql:
            return {"cnt": 0}
        return real_fetch_one(sql, params)

    monkeypatch.setattr(chunked_db, "fetch_one", stale_count)
    mgr = _manager(chunked_db, tmp_path)

    session = mgr.create_session(
        user_id=user_id,
        filename="overflow.bin",
        content_type="application/octet-stream",
        total_size=1024,
    )

    assert session is None, "stale advisory count let an over-cap session through"
    assert _active_count(chunked_db, user_id) == _CAP
    assert os.listdir(mgr._temp_dir) == [], "rejected create orphaned a temp file"


def test_under_cap_create_still_succeeds(chunked_db, tmp_path):
    """Control: the atomic guard must not block a legitimate create."""
    user_id = 424243
    _seed_active(chunked_db, user_id, _CAP - 1)
    mgr = _manager(chunked_db, tmp_path)

    session = mgr.create_session(
        user_id=user_id,
        filename="legit.bin",
        content_type="application/octet-stream",
        total_size=1024,
    )

    assert session is not None
    assert _active_count(chunked_db, user_id) == _CAP
    assert os.path.isfile(session.temp_path)


def test_create_session_uses_guarded_insert_source_invariant():
    """Ratchet: create_session must keep the atomic guarded INSERT.

    A plain ``INSERT ... VALUES`` after a separate COUNT check reopens the
    TOCTOU. Require the guarded ``INSERT ... SELECT ... WHERE (SELECT COUNT)``
    statement and the PostgreSQL advisory-lock serialization.
    """
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))

    atomic = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_ATOMIC_SESSION_INSERT_SQL"
            for t in node.targets
        ):
            atomic = node.value
    assert atomic is not None, "atomic guarded INSERT statement missing"

    sql = ast.literal_eval(atomic)
    upper = sql.upper()
    assert "INSERT INTO MEDIA_UPLOAD_SESSIONS" in upper
    assert "SELECT" in upper
    assert "WHERE" in upper
    assert "COUNT(" in upper, "quota guard subquery missing from INSERT"

    create_fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "create_session"
    )
    rendered = ast.dump(create_fn)
    assert "pg_advisory_xact_lock" in rendered, "Postgres serialization missing"
    assert "_ATOMIC_SESSION_INSERT_SQL" in rendered, "guarded INSERT not used"
