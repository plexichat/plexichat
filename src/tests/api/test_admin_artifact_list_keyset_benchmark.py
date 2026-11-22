"""Benchmark + parity: deep-page cost of ``GET /admin/artifacts``.

``_list_with_total`` (the query behind the admin artifact list) paged with
``ORDER BY a.created_at DESC LIMIT ? OFFSET ?``. SQLite walks ``idx_artifacts_created``
from the start and discards every row before ``OFFSET``, so a deep page costs
O(offset) VM work while a shallow page costs O(page size).

The benchmark counts SQLite virtual-machine operations for the page query via
``Connection.set_progress_handler`` -- a deterministic proxy for rows scanned
that does not flake under load. It seeds a real SQLite table with the production
indexes and exercises the real route helper.

The keyset tests below deliberately call ``_list_with_total`` with the new
``before`` cursor argument. On the unfixed tree that argument does not exist, so
they fail (TypeError); after the fix they must pass with flat cost and page
parity. Run with: pytest -n 0 src/tests/api/test_admin_artifact_list_keyset_benchmark.py
"""

from __future__ import annotations

import sqlite3

import pytest

from src.api.routes.admin import artifacts as artifacts_route

pytestmark = pytest.mark.unit

N = 20_000
LIMIT = 50
DEEP_PAGE = N // LIMIT
DEEP_OFFSET = (DEEP_PAGE - 1) * LIMIT

_SCHEMA = """
CREATE TABLE artifacts (
    id INTEGER PRIMARY KEY,
    author_id INTEGER NOT NULL,
    artifact_type TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    payload TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    rev INTEGER NOT NULL DEFAULT 0
);
-- Production indexes at the time of the finding (see core/artifacts/schema.py).
CREATE INDEX idx_artifacts_created ON artifacts(created_at);
-- Added by the fix: indexed (created_at, id) tuple for the keyset seek.
CREATE INDEX idx_artifacts_created_id ON artifacts(created_at, id);
"""


class VMCounterDB:
    """Minimal DB adapter counting VM ops executed by each ``fetch_all``."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.last_ops = 0
        self.last_sql = ""
        self.last_params = ()

    def execute(self, sql, params=None):
        return self.conn.execute(sql, params or ())

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
            self.last_sql = " ".join(sql.split())
            self.last_params = tuple(params or ())
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
            7,
            "upload",
            "title",
            "completed",
            "{}",
            base + i,
            base + i,
            0,
        )
        for i in range(N)
    ]
    conn.executemany(
        "INSERT INTO artifacts (id, author_id, artifact_type, title, status, "
        "payload, created_at, updated_at, rev) VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return VMCounterDB(conn)


def _offset_page_ops(db, offset: int) -> int:
    artifacts_route._list_with_total(db, LIMIT, offset)
    return db.last_ops


def _cursor_at_offset(db, offset: int) -> str:
    """Keyset cursor of the row just before a given OFFSET page."""
    row = db.conn.execute(
        "SELECT created_at, id FROM artifacts "
        "ORDER BY created_at DESC, id DESC LIMIT 1 OFFSET ?",
        (offset - 1,),
    ).fetchone()
    return f"{row[0]}:{row[1]}"


def test_offset_deep_page_scans_far_more_than_shallow(seeded_db):
    """BEFORE the fix: OFFSET makes a deep page walk the whole index.

    Still holds on the fixed tree -- callers that omit the cursor keep the
    legacy OFFSET path, so this documents *why* the keyset path exists.
    """
    shallow = _offset_page_ops(seeded_db, 0)
    deep = _offset_page_ops(seeded_db, DEEP_OFFSET)
    print(
        f"\nOFFSET page-query VM ops: shallow={shallow} deep={deep} "
        f"ratio={deep / max(shallow, 1):.1f}x N={N} limit={LIMIT}"
    )
    assert deep > shallow * 10, (shallow, deep)


def test_keyset_deep_page_cost_is_flat(seeded_db):
    """AFTER the fix: seeking by (created_at, id) makes depth irrelevant."""
    shallow = _offset_page_ops(seeded_db, 0)
    cursor = _cursor_at_offset(seeded_db, DEEP_OFFSET)
    artifacts_route._list_with_total(seeded_db, LIMIT, 0, cursor)
    deep = seeded_db.last_ops
    print(
        f"\nKEYSET page-query VM ops: shallow={shallow} deep={deep} "
        f"ratio={deep / max(shallow, 1):.2f}x N={N} limit={LIMIT}"
    )
    assert "OFFSET" not in seeded_db.last_sql.upper()
    assert "(created_at, id) < (?, ?)" in seeded_db.last_sql
    assert deep <= shallow * 3, (shallow, deep)


def test_keyset_page_matches_offset_page_and_total(seeded_db):
    """AFTER the fix: the keyset page is byte-for-byte the old OFFSET page."""
    offset_items, offset_total = artifacts_route._list_with_total(
        seeded_db, LIMIT, DEEP_OFFSET
    )
    cursor = _cursor_at_offset(seeded_db, DEEP_OFFSET)
    key_items, key_total = artifacts_route._list_with_total(seeded_db, LIMIT, 0, cursor)
    assert [int(a.id) for a in key_items] == [int(a.id) for a in offset_items]
    assert key_total == offset_total == N


def test_keyset_rejects_malformed_cursor(seeded_db):
    """AFTER the fix: a malformed/oversized cursor is rejected before SQL."""
    with pytest.raises(ValueError):
        artifacts_route._list_with_total(seeded_db, LIMIT, 0, "not-a-cursor")
    with pytest.raises(ValueError):
        artifacts_route._list_with_total(
            seeded_db, LIMIT, 0, "1700000000000:99999999999999999999"
        )
