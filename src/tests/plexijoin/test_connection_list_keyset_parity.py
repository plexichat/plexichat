"""Regression guards for the additive keyset cursor on connection listing.

``PlexiJoinManager.list_connections`` keeps its OFFSET/paged contract untouched
and gains an opt-in ``before`` cursor on the indexed ``(created_at, id)`` tuple.
These tests pin:

* result parity -- a keyset page is exactly the page the OFFSET query returned,
  with the same response keys and the same ``created_at DESC`` ordering;
* bounded cost -- the keyset branch is one statement with a tuple range seek
  and a bound ``LIMIT ?`` and no OFFSET walk, regardless of page depth;
* preserved filters and cursor validation before any DB access.

The HTTP route's response contract is unchanged: the cursor is a request-only
parameter built by the client from the ``created_at`` / ``id`` already present
on every returned connection.

Run with: pytest -n 0 src/tests/plexijoin/test_connection_list_keyset_parity.py
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, Request as FastAPIRequest

from src.api.routes.admin import plexijoin as admin_route
from src.core.plexijoin.manager import PlexiJoinManager
from src.core.security.hardening import HardeningViolation

pytestmark = pytest.mark.unit

N = 120
BASE = 1_700_000_000_000


def _cursor(conn: dict) -> str:
    return f"{conn['created_at']}:{conn['id']}"


class CountingDB:
    """Real SQLite connection that records page SQL/params and VM ops."""

    def __init__(self, conn):
        self.conn = conn
        self.last_ops = 0
        self.last_sql = ""
        self.last_params = ()

    def execute(self, sql, params=None):
        return self.conn.execute(sql, params or ())

    def fetch_one(self, sql, params=None):
        cur = self.conn.execute(sql, params or ())
        cols = [d[0] for d in cur.description] if cur.description else []
        row = cur.fetchone()
        cur.close()
        return dict(zip(cols, row)) if row is not None else None

    def fetch_all(self, sql, params=None):
        self.last_sql = sql
        self.last_params = tuple(params or ())
        counter = {"ops": 0}

        def _progress():
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
    conn.executescript(
        """
        CREATE TABLE plexijoin_connections (
            id INTEGER PRIMARY KEY, remote_instance_id TEXT NOT NULL UNIQUE,
            remote_url TEXT NOT NULL, shared_key_encrypted TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', connected_at INTEGER,
            messages_in INTEGER DEFAULT 0, messages_out INTEGER DEFAULT 0,
            last_activity INTEGER, note TEXT, created_at INTEGER NOT NULL,
            created_by INTEGER NOT NULL);
        CREATE INDEX idx_c_created_id ON plexijoin_connections(created_at, id);
        CREATE INDEX idx_c_status_created_id ON plexijoin_connections(status, created_at, id);
        CREATE INDEX idx_c_created ON plexijoin_connections(created_at);
        CREATE INDEX idx_c_status_created ON plexijoin_connections(status, created_at);
        """
    )
    rows = [
        (
            1000 + i,
            f"remote-{i}",
            f"https://remote{i}.example.com",
            f"enc:secret-{i}",
            "active" if i % 2 else "pending",
            BASE + i,
            i,
            i * 2,
            BASE + i,
            "note",
            BASE + i,
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
    return CountingDB(conn)


def test_keyset_page_matches_offset_page_and_contract(seeded_db):
    mgr = PlexiJoinManager(seeded_db)
    prefix = mgr.list_connections(page=1, per_page=10, decrypt_keys=False)
    cursor = _cursor(prefix["connections"][-1])

    expected = mgr.list_connections(page=2, per_page=10, decrypt_keys=False)
    got = mgr.list_connections(page=2, per_page=10, before=cursor, decrypt_keys=False)

    assert got == expected
    assert list(got) == ["connections", "total", "page", "per_page", "pages"]
    assert got["total"] == N and got["page"] == 2 and got["per_page"] == 10
    # Bounded, seeked statement: no OFFSET walk, tuple predicate, bound LIMIT.
    sql = seeded_db.last_sql
    assert "(created_at, id) < (?, ?)" in sql
    assert "ORDER BY created_at DESC, id DESC" in sql
    assert "OFFSET" not in sql.upper()
    assert seeded_db.last_params == (BASE + N - 10, 1000 + N - 10, 10)


def test_keyset_preserves_status_filter(seeded_db):
    mgr = PlexiJoinManager(seeded_db)
    prefix = mgr.list_connections(
        status="active", page=1, per_page=7, decrypt_keys=False
    )
    cursor = _cursor(prefix["connections"][-1])
    expected = mgr.list_connections(
        status="active", page=2, per_page=7, decrypt_keys=False
    )
    got = mgr.list_connections(
        status="active", page=2, per_page=7, before=cursor, decrypt_keys=False
    )
    assert got == expected
    assert "status = ?" in seeded_db.last_sql
    assert seeded_db.last_params[0] == "active"
    assert all(c["status"] == "active" for c in got["connections"])


def test_existing_offset_path_unchanged(seeded_db):
    """Old callers keep the exact statement and response shape."""
    mgr = PlexiJoinManager(seeded_db)
    page = mgr.list_connections(page=2, per_page=10, decrypt_keys=False)
    assert page["page"] == 2 and page["per_page"] == 10
    assert "LIMIT ? OFFSET ?" in seeded_db.last_sql
    assert "(created_at, id) <" not in seeded_db.last_sql
    assert seeded_db.last_params == (10, 10)


@contextmanager
def _route_env(db, seen):
    """Patch the hot route's auth/licence/DB deps; leave endpoint logic real."""
    req = MagicMock(spec=FastAPIRequest)

    def _permission(_request, permission):
        seen.append(permission)
        return 1

    patches = [
        patch.object(admin_route, "check_host_restriction"),
        patch.object(admin_route, "require_admin_permission", side_effect=_permission),
        patch("src.api.get_db", return_value=db),
        patch("utils.licensing.has_feature", return_value=True),
    ]
    for p in patches:
        p.start()
    try:
        yield req
    finally:
        for p in reversed(patches):
            p.stop()


def test_route_keyset_preserves_scope_and_response_contract(seeded_db):
    """The hot route uses the cursor without changing authz or response shape."""
    seen = []
    mgr = PlexiJoinManager(seeded_db)
    prefix = mgr.list_connections(page=1, per_page=10, decrypt_keys=False)
    cursor = _cursor(prefix["connections"][-1])

    with _route_env(seeded_db, seen) as req:
        got = asyncio.run(
            admin_route.list_connections(
                req, status=None, page=2, per_page=10, before=cursor
            )
        )
        expected = asyncio.run(
            admin_route.list_connections(req, status=None, page=2, per_page=10)
        )

    # Scope gate still runs on the keyset path (viewer authz unchanged).
    assert seen == ["admin.plexijoin.manage", "admin.plexijoin.manage"]
    assert got == expected
    assert list(got) == ["connections", "total", "page", "per_page", "pages"]


def test_route_rejects_invalid_cursor_with_400(seeded_db):
    seen = []
    with _route_env(seeded_db, seen) as req:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(admin_route.list_connections(req, before="not-a-cursor"))
    assert exc.value.status_code == 400
    assert seen == ["admin.plexijoin.manage"]  # gate runs before validation


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "abc",
        "1",
        "1:",
        ":2",
        "1:2:3",
        "-1:5",
        "١:٥",
        "1_0:2",
        "9223372036854775808:1",
        "1:" + "9" * 5000,
        42,
        True,
    ],
)
def test_invalid_cursor_rejected_before_db(seeded_db, bad):
    before = seeded_db.last_sql
    with pytest.raises(HardeningViolation):
        PlexiJoinManager(seeded_db).list_connections(before=bad, decrypt_keys=False)
    assert seeded_db.last_sql == before
