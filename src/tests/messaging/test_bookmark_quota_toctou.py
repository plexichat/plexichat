"""Regression: ``add_bookmark``'s quota check must be atomic with its INSERT.

The advisory ``SELECT COUNT(*)`` fast-fail may be stale: under PostgreSQL
``READ COMMITTED`` (or with a second worker crossing the cap between the count
and the write) a request can observe ``count < cap`` when the table already
holds ``cap`` rows. Before the fix the following bare ``INSERT`` then pushed
the user past ``MAX_BOOKMARKS_PER_USER``.

The fix makes the INSERT self-guarding -- ``INSERT ... SELECT ... WHERE
(SELECT COUNT(*)) < cap`` -- and relies on ``cursor.rowcount == 0`` to report
a denied insert. SQLite serializes writers so the single statement is atomic;
PostgreSQL takes a transaction-scoped advisory lock in a fresh namespace,
keyed by user, so the second concurrent create sees the first one's commit.

Run with: pytest src/tests/messaging/test_bookmark_quota_toctou.py -q -n 0
"""

import pytest

from src.core.messaging.services.bookmarks import BookmarkService


_CAP = BookmarkService.MAX_BOOKMARKS_PER_USER


class _Cursor:
    def __init__(self, rowcount):
        self.rowcount = rowcount

    def close(self):
        pass


class _StaleCountDB:
    """Fake DB whose advisory COUNT is frozen at a deliberately stale value.

    ``execute`` emulates the two real backends: a *guarded* INSERT (one that
    carries a ``COUNT(*)`` predicate) inserts only while the REAL row count is
    below the cap; the pre-fix bare INSERT always inserts. That is what makes
    the probe non-vacuous: the identical fake accepts the overflow on the old
    code path and denies it on the fixed one.
    """

    def __init__(self, real_rows, stale_count, db_type="sqlite"):
        self.type = db_type
        self.cap = _CAP
        self.rows = list(real_rows)
        self.stale_count = stale_count
        self.statements = []
        self.begun = 0
        self.committed = 0
        self.rollbacks = 0

    def fetch_one(self, query, params=()):
        if "COUNT(*)" in query.upper():
            return (self.stale_count,)  # stale fast-fail snapshot
        return None  # no pre-existing bookmark row

    def fetch_all(self, query, params=()):
        return []

    def begin_transaction(self):
        self.begun += 1

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rollbacks += 1

    def execute(self, query, params=(), auto_commit=True):
        self.statements.append((query, params))
        upper = query.strip().upper()
        if "PG_ADVISORY_XACT_LOCK" in upper:
            return _Cursor(1)
        if upper.startswith("INSERT"):
            guarded = "COUNT(*)" in upper
            if (not guarded) or len(self.rows) < self.cap:
                self.rows.append(params)
                return _Cursor(1)
            return _Cursor(0)  # cap reached -> the DB inserted nothing
        return _Cursor(1)


def _full_rows():
    return [object()] * _CAP


def _service(db):
    svc = BookmarkService(db)
    # The probe targets quota enforcement, not the authz/enrichment path.
    svc._ensure_bookmark_access = lambda *a, **k: None
    svc._generate_id = lambda: 4242
    svc._get_timestamp = lambda: 1700000000000
    return svc


def _insert_statements(db):
    return [q for q, _ in db.statements if q.strip().upper().startswith("INSERT")]


@pytest.mark.messaging
class TestBookmarkQuotaAtomicity:
    def test_stale_count_at_cap_is_denied(self):
        """At the cap with a stale advisory count, no row may be inserted."""
        db = _StaleCountDB(real_rows=_full_rows(), stale_count=0)
        svc = _service(db)

        with pytest.raises(ValueError, match="Maximum 200 bookmarks per user"):
            svc.add_bookmark(7, 100, 55)

        assert len(db.rows) == _CAP  # no overflow past the cap
        inserts = _insert_statements(db)
        assert len(inserts) == 1
        # The INSERT must re-count inside itself; a bare INSERT (old behaviour)
        # is exactly what the stale-count probe is meant to catch.
        assert "COUNT(*)" in inserts[0].upper()

    def test_below_cap_still_inserts(self):
        """Positive control: below the cap a bookmark is still created."""
        db = _StaleCountDB(real_rows=[object()] * (_CAP - 1), stale_count=0)
        svc = _service(db)

        svc.add_bookmark(7, 100, 55)

        assert len(db.rows) == _CAP
        assert len(_insert_statements(db)) == 1

    def test_postgres_uses_fresh_namespace_advisory_lock(self):
        """PostgreSQL serializes per user around the guarded INSERT."""
        db = _StaleCountDB(real_rows=_full_rows(), stale_count=0, db_type="postgres")
        svc = _service(db)

        with pytest.raises(ValueError, match="Maximum 200 bookmarks per user"):
            svc.add_bookmark(7, 100, 55)

        lock_stmts = [
            (i, q, p)
            for i, (q, p) in enumerate(db.statements)
            if "PG_ADVISORY_XACT_LOCK" in q.upper()
        ]
        assert len(lock_stmts) == 1
        lock_i, lock_sql, lock_params = lock_stmts[0]
        insert_i = next(
            i
            for i, (q, _) in enumerate(db.statements)
            if q.strip().upper().startswith("INSERT")
        )
        assert lock_i < insert_i  # lock is acquired before the guarded INSERT

        namespace = BookmarkService._quota_lock_key(7)[0]
        assert lock_params[0] == namespace
        assert namespace != 12345  # not the migration lock namespace

        # Transaction-scoped: wrapped by begin/commit with no rollback.
        assert (db.begun, db.committed, db.rollbacks) == (1, 1, 0)
