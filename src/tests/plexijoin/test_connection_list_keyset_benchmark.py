"""Benchmark: deep-page cost of the hot admin PlexiJoin connection list.

``PlexiJoinManager.list_connections`` (used by ``GET /admin/plexijoin/connections``,
explicitly documented as a hot endpoint) addressed pages with
``ORDER BY created_at DESC LIMIT ? OFFSET ?``. SQLite walks and discards every
row before ``OFFSET``, so a deep page costs O(offset) VM work while a shallow
page costs O(page size).

The benchmark counts SQLite virtual-machine operations for the *page query*
(via ``Connection.set_progress_handler``), which is a deterministic proxy for
rows scanned -- unlike wall-clock timing it does not flake under load. The
test seeds a real SQLite table and exercises the real manager.

Run with: pytest -n 0 src/tests/plexijoin/test_connection_list_keyset_benchmark.py
"""

from __future__ import annotations

import sqlite3

import pytest

from src.core.plexijoin.manager import PlexiJoinManager

pytestmark = pytest.mark.unit

N = 20_000
PER_PAGE = 50
DEEP_PAGE = N // PER_PAGE

_SCHEMA = """
CREATE TABLE plexijoin_connections (
    id INTEGER PRIMARY KEY,
    remote_instance_id TEXT NOT NULL UNIQUE,
    remote_url TEXT NOT NULL,
    shared_key_encrypted TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    connected_at INTEGER,
    messages_in INTEGER DEFAULT 0,
    messages_out INTEGER DEFAULT 0,
    last_activity INTEGER,
    note TEXT,
    created_at INTEGER NOT NULL,
    created_by INTEGER NOT NULL
);
-- The production indexes at the time of the finding (see schema.py).
CREATE INDEX idx_pc_created ON plexijoin_connections(created_at);
CREATE INDEX idx_pc_status_created ON plexijoin_connections(status, created_at);
-- Added by the fix: indexed (created_at, id) tuple for the keyset seek.
CREATE INDEX idx_pc_created_id ON plexijoin_connections(created_at, id);
CREATE INDEX idx_pc_status_created_id ON plexijoin_connections(status, created_at, id);
"""


class VMCounterDB:
    """Minimal DB adapter over a raw sqlite3 connection.

    Counts VM ops executed by each ``fetch_all`` call via the progress handler,
    so the number reflects how much work the page query actually did.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.last_ops = 0

    def execute(self, sql, params=None):
        return self.conn.execute(sql, params or ())

    def fetch_one(self, sql, params=None):
        cur = self.conn.execute(sql, params or ())
        cols = [d[0] for d in cur.description] if cur.description else []
        row = cur.fetchone()
        cur.close()
        return dict(zip(cols, row)) if row is not None else None

    def fetch_all(self, sql, params=None):
        counter = {"ops": 0}

        def _progress() -> int:
            counter["ops"] += 1
            return 0

        self.conn.set_progress_handler(_progress, 1)
        try:
            cur = self.conn.execute(sql, params or ())
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchall()
            cur.close()
            self.last_ops = counter["ops"]
            return [dict(zip(cols, r)) for r in rows]
        finally:
            self.conn.set_progress_handler(None, 0)


@pytest.fixture
def seeded_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    base = 1_700_000_000_000
    rows = [
        (
            1000 + i,
            f"remote-{i}",
            f"https://remote{i}.example.com",
            f"enc:secret-{i}",
            "active" if i % 2 else "pending",
            base + i,
            i,
            i * 2,
            base + i,
            "note",
            base + i,  # distinct created_at, ascending with id
            42,
        )
        for i in range(N)
    ]
    conn.executemany(
        "INSERT INTO plexijoin_connections (id, remote_instance_id, remote_url, "
        "shared_key_encrypted, status, connected_at, messages_in, messages_out, "
        "last_activity, note, created_at, created_by) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return VMCounterDB(conn)


def _page_ops(db, **kwargs) -> int:
    mgr = PlexiJoinManager(db)
    mgr.list_connections(decrypt_keys=False, **kwargs)
    return db.last_ops


def _cursor_at_offset(db, offset: int) -> str:
    """Keyset cursor of the row just before a given OFFSET page."""
    row = db.conn.execute(
        "SELECT created_at, id FROM plexijoin_connections "
        "ORDER BY created_at DESC, id DESC LIMIT 1 OFFSET ?",
        (offset - 1,),
    ).fetchone()
    return f"{row[0]}:{row[1]}"


def test_offset_deep_page_scans_far_more_than_shallow(seeded_db):
    """BEFORE the fix: OFFSET makes a deep page walk the whole index.

    This assertion documents *why* the keyset path exists and still holds on
    the fixed tree -- callers that omit the cursor keep the legacy OFFSET path.
    """
    shallow = _page_ops(seeded_db, page=1, per_page=PER_PAGE)
    deep = _page_ops(seeded_db, page=DEEP_PAGE, per_page=PER_PAGE)
    print(
        f"\nOFFSET page-query VM ops: shallow={shallow} deep={deep} "
        f"ratio={deep / max(shallow, 1):.1f}x N={N} per_page={PER_PAGE}"
    )
    assert deep > shallow * 10, (shallow, deep)


def test_keyset_deep_page_cost_is_flat(seeded_db):
    """AFTER the fix: seeking by (created_at, id) makes depth irrelevant."""
    shallow = _page_ops(seeded_db, page=1, per_page=PER_PAGE)
    deep_offset = (DEEP_PAGE - 1) * PER_PAGE
    cursor = _cursor_at_offset(seeded_db, deep_offset)
    deep = _page_ops(seeded_db, page=DEEP_PAGE, per_page=PER_PAGE, before=cursor)
    print(
        f"\nKEYSET page-query VM ops: shallow={shallow} deep={deep} "
        f"ratio={deep / max(shallow, 1):.2f}x N={N} per_page={PER_PAGE}"
    )
    assert deep <= shallow * 3, (shallow, deep)
