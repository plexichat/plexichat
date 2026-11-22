"""DSAR admin decisions are a guarded state machine, not a read-then-clobber.

An admin approve/deny first reads the request and checks ``status ==
'pending'``. That read is only advisory: a concurrent admin decision (or the
harvester / cancel path) can move the row out of 'pending' between the SELECT
and the UPDATE. The authoritative transition must therefore be a conditional
UPDATE with a rowcount check, so a late decision can never overwrite a newer
one (e.g. a denial silently flipped back to 'approved', authorising an export
that was deliberately refused).

Regression for the gap where approve_request/deny_request used an unconditional
UPDATE after the advisory read.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.dsar.manager import DSARManager


class _Cursor:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _RacyDB:
    """DB double that enforces ``UPDATE ... WHERE status = 'pending'``.

    The advisory SELECT always observes a pending row. ``competing_status`` is
    the state a concurrent transition commits *before* our UPDATE lands. The
    fake then applies the same semantics a real SQL conditional UPDATE would:
    a guarded UPDATE sees a non-pending row and reports rowcount 0, while an
    unguarded UPDATE clobbers it and reports rowcount 1.
    """

    def __init__(self, competing_status="pending"):
        self.row = {
            "id": 77,
            "user_id": 5,
            "status": "pending",
            "format": "json",
        }
        self.competing_status = competing_status
        self.executed = []

    def fetch_one(self, query, params=None):
        self.executed.append(("read", query, tuple(params or ())))
        return dict(self.row)

    def execute(self, query, params=None):
        self.executed.append(("write", query, tuple(params or ())))
        normalized = " ".join(str(query).lower().split())
        if "update dsar_requests" in normalized:
            # A competing decision commits just before our write.
            self.row["status"] = self.competing_status
            guarded = "status = 'pending'" in normalized
            if guarded and self.row["status"] != "pending":
                return _Cursor(0)
            # Unguarded write (the old behaviour) clobbers the newer state.
            if "status = 'approved'" in normalized:
                self.row["status"] = "approved"
            elif "status = 'denied'" in normalized:
                self.row["status"] = "denied"
            return _Cursor(1)
        return _Cursor(1)

    def status_updates(self):
        return [
            " ".join(q.lower().split())
            for kind, q, _ in self.executed
            if kind == "write" and "update dsar_requests" in q.lower()
        ]


def _manager(db):
    return DSARManager(db)


class TestApproveDecisionGuard:
    def test_approve_write_is_conditional_on_pending(self):
        db = _RacyDB(competing_status="pending")
        mgr = _manager(db)
        with patch.object(mgr, "_get_audit_log", return_value=MagicMock()):
            result = mgr.approve_request(77, 99)
        assert result["status"] == "approved"
        assert db.status_updates(), "the approve must issue a status UPDATE"
        assert all("status = 'pending'" in sql for sql in db.status_updates()), (
            f"approve UPDATE must be conditional on pending: {db.status_updates()}"
        )

    def test_approve_losing_a_race_fails_closed(self):
        db = _RacyDB(competing_status="denied")
        mgr = _manager(db)
        audit = MagicMock()
        with patch.object(mgr, "_get_audit_log", return_value=audit):
            with pytest.raises(ValueError, match="changed state concurrently"):
                mgr.approve_request(77, 99)
        # No side effects may be recorded for a decision that did not land.
        audit.log_event.assert_not_called()
        assert db.row["status"] == "denied"


class TestDenyDecisionGuard:
    def test_deny_write_is_conditional_on_pending(self):
        db = _RacyDB(competing_status="pending")
        mgr = _manager(db)
        with patch.object(mgr, "_get_audit_log", return_value=MagicMock()):
            result = mgr.deny_request(77, 99, "test denial")
        assert result["status"] == "denied"
        assert db.status_updates(), "the deny must issue a status UPDATE"
        assert all("status = 'pending'" in sql for sql in db.status_updates()), (
            f"deny UPDATE must be conditional on pending: {db.status_updates()}"
        )

    def test_deny_losing_a_race_fails_closed(self):
        db = _RacyDB(competing_status="approved")
        mgr = _manager(db)
        audit = MagicMock()
        with patch.object(mgr, "_get_audit_log", return_value=audit):
            with pytest.raises(ValueError, match="changed state concurrently"):
                mgr.deny_request(77, 99, "late denial")
        audit.log_event.assert_not_called()
        assert db.row["status"] == "approved"
