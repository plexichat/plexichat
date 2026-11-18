"""Regression tests: bound admin read helpers that could dump unbounded rows.

Two helpers in the admin read paths forwarded an unbounded row set straight
into a response:

* ``get_device_trust_overview`` enumerated every user with a device or live
  session (no SQL ``LIMIT``) and then issued two more queries per user — one
  admin GET scaled linearly with the size of ``auth_devices``/``auth_sessions``.
* ``core.admin.tickets.get_feedback_tickets`` forwarded ``limit``/``offset``
  verbatim (SQLite reads ``LIMIT -1`` as *unlimited*), and
  ``get_ticket_notes`` had no page bound at all because its route exposes no
  pagination parameter.

Both now clamp their row sets to a fixed ceiling.
"""

from src.api.routes.admin.device_overview import (
    MAX_OVERVIEW_USERS,
    get_device_trust_overview,
)
from src.core.admin import tickets


class _EnumerationDB:
    """Record the enumeration SQL and emit a fixed list of user ids."""

    def __init__(self, user_ids):
        self.query = None
        self.params = None
        self._user_ids = user_ids

    def fetch_all(self, query, params=None):
        self.query = query
        self.params = params
        return [{"user_id": uid} for uid in self._user_ids]


class _OverviewMgr:
    """Minimal auth-manager double; devices/sessions are empty."""

    def __init__(self, db):
        self._db = db
        self._config = {}
        self.device_calls = 0
        self.session_calls = 0

    def _get_timestamp(self):
        return 1000

    def get_devices(self, uid):
        self.device_calls += 1
        return []

    def get_sessions(self, uid):
        self.session_calls += 1
        return []


class _RecordingDB:
    def __init__(self, rows=None):
        self.calls = []
        self._rows = rows or []

    def fetch_all(self, query, params=None):
        self.calls.append((query, params))
        return list(self._rows)


class TestDeviceOverviewIsBounded:
    def test_enumeration_query_has_a_limit(self):
        db = _EnumerationDB([1, 2, 3])
        mgr = _OverviewMgr(db)
        get_device_trust_overview(mgr, mgr._config)
        assert "LIMIT ?" in db.query
        assert db.params == (MAX_OVERVIEW_USERS + 1,)

    def test_user_set_is_capped(self):
        # Far more users than the cap: only MAX_OVERVIEW_USERS may be expanded
        # into the per-user get_devices/get_sessions fan-out.
        db = _EnumerationDB(list(range(MAX_OVERVIEW_USERS + 100)))
        mgr = _OverviewMgr(db)
        overview = get_device_trust_overview(mgr, mgr._config)
        assert len(overview["users"]) == MAX_OVERVIEW_USERS
        assert mgr.device_calls == MAX_OVERVIEW_USERS
        assert mgr.session_calls == MAX_OVERVIEW_USERS
        assert overview["totals"]["users"] == MAX_OVERVIEW_USERS


class TestTicketReadsAreBounded:
    def test_feedback_tickets_negative_limit_is_empty(self, monkeypatch):
        monkeypatch.setattr(tickets.config, "get", lambda *a, **k: False)
        db = _RecordingDB()
        tickets.get_feedback_tickets(db, limit=-1, offset=-5)
        assert db.calls[-1][1] == (0, 0)

    def test_feedback_tickets_limit_is_capped(self, monkeypatch):
        monkeypatch.setattr(tickets.config, "get", lambda *a, **k: False)
        db = _RecordingDB()
        tickets.get_feedback_tickets(db, limit=10**9, offset=10**9)
        limit, offset = db.calls[-1][1]
        assert limit == tickets.MAX_PAGE_SIZE
        assert offset == 10**9

    def test_ticket_notes_query_is_bounded(self):
        db = _RecordingDB()
        tickets.get_ticket_notes(db, 123)
        query, params = db.calls[-1]
        assert "LIMIT ?" in query
        assert params == (123, tickets.MAX_TICKET_NOTES)
