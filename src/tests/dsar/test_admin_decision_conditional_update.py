"""DSAR admin approve/deny must use a guarded state transition.

The advisory ``SELECT ... WHERE status = 'pending'`` in ``approve_request`` /
``deny_request`` can go stale: a concurrent admin decision, the harvester, or
the user's cancel can move the row before the write lands. A plain
``UPDATE ... WHERE id = ?`` would then clobber the newer state -- e.g. a late
approval flipping a denial back to 'approved' and authorising an export that
was deliberately refused. The authoritative write must be conditional on the
row still being 'pending', with a rowcount check that fails closed before any
cache/audit side effect.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.dsar.manager import DSARManager


class _Cursor:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


class _RacyDB:
    """DB double modelling a transition that commits between read and write.

    ``fetch_one`` always returns the stale 'pending' row observed by the
    advisory read. On the first status UPDATE a competing decision commits
    (``competing_status``). A guarded UPDATE (``status = 'pending'``) then sees
    rowcount 0 and leaves the row untouched; an unguarded one would clobber it.
    """

    def __init__(self, competing_status: str):
        self.row = {"id": 77, "user_id": 5, "status": "pending", "format": "json"}
        self.competing_status = competing_status
        self.write_sql: list[str] = []

    def fetch_one(self, query, params=None):
        return dict(self.row)

    def execute(self, query, params=None):
        normalized = " ".join(str(query).lower().split())
        if "update dsar_requests" not in normalized:
            return _Cursor(1)
        self.write_sql.append(normalized)
        self.row["status"] = self.competing_status
        guarded = "status = 'pending'" in normalized
        if guarded and self.competing_status != "pending":
            return _Cursor(0)  # lost the race: row stays at competing_status
        if "status = 'approved'" in normalized:
            self.row["status"] = "approved"
        elif "status = 'denied'" in normalized:
            self.row["status"] = "denied"
        return _Cursor(1)


def _mgr(db):
    return DSARManager(db)


class TestApproveGuarded:
    def test_write_is_conditional_on_pending(self):
        db = _RacyDB(competing_status="pending")
        mgr = _mgr(db)
        with patch.object(mgr, "_get_audit_log", return_value=MagicMock()):
            result = mgr.approve_request(77, 99)
        assert result["status"] == "approved"
        assert db.write_sql
        assert all("status = 'pending'" in sql for sql in db.write_sql)

    def test_lost_race_fails_closed_without_side_effects(self):
        db = _RacyDB(competing_status="denied")
        mgr = _mgr(db)
        audit = MagicMock()
        with patch.object(mgr, "_get_audit_log", return_value=audit):
            with pytest.raises(ValueError, match="changed state concurrently"):
                mgr.approve_request(77, 99)
        audit.log_event.assert_not_called()
        assert db.row["status"] == "denied"


class TestDenyGuarded:
    def test_write_is_conditional_on_pending(self):
        db = _RacyDB(competing_status="pending")
        mgr = _mgr(db)
        with patch.object(mgr, "_get_audit_log", return_value=MagicMock()):
            result = mgr.deny_request(77, 99, "test denial")
        assert result["status"] == "denied"
        assert db.write_sql
        assert all("status = 'pending'" in sql for sql in db.write_sql)

    def test_lost_race_fails_closed_without_side_effects(self):
        db = _RacyDB(competing_status="approved")
        mgr = _mgr(db)
        audit = MagicMock()
        with patch.object(mgr, "_get_audit_log", return_value=audit):
            with pytest.raises(ValueError, match="changed state concurrently"):
                mgr.deny_request(77, 99, "late denial")
        audit.log_event.assert_not_called()
        assert db.row["status"] == "approved"
