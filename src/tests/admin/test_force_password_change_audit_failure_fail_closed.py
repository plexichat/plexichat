"""A failed audit write must never allow force_password_change to mutate.

``force_password_change_target`` guards the privileged UPDATE with an
"audit-first" invariant: if the audit row cannot be written, the mutation must
not happen (or must be rolled back).  Both branches detected an audit failure
only when ``log_admin_action`` *raised*.  In reality that helper swallows its
own DB/file errors and returns ``False``, so a failed audit write let the
UPDATE through with no audit row - a security-relevant record suppressed.

These tests pin both driver paths (transactional and autocommit) to fail closed
when the audit write reports failure, while keeping the success path working.
"""

import pytest

from src.core.admin.auth.password_mixin import force_password_change_target

pytestmark = [pytest.mark.security, pytest.mark.unit]


class _BaseDB:
    def __init__(self):
        self.updated = False

    def fetch_one(self, sql, params):
        # Target admin exists.
        return {"id": params[0]}

    def execute(self, sql, params):
        if "UPDATE admin_users SET force_password_change" in sql:
            self.updated = True


class _AutocommitDB(_BaseDB):
    """No begin_transaction/commit: exercises the audit-first branch."""


class _TransactionalDB(_BaseDB):
    def __init__(self):
        super().__init__()
        self.began = False
        self.committed = False
        self.rolled_back = False

    def begin_transaction(self):
        self.began = True

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


@pytest.fixture
def audit_result(monkeypatch):
    """Control what the shared audit helper reports, capturing its calls."""
    calls = []

    def _set(result):
        def _fake(*args, **kwargs):
            calls.append((args, kwargs))
            return result

        monkeypatch.setattr(
            "src.core.admin.permissions.log_admin_action", _fake, raising=False
        )
        return calls

    return _set


def test_autocommit_audit_failure_blocks_mutation(monkeypatch, audit_result):
    calls = audit_result(False)  # audit write failed (helper swallowed error)
    db = _AutocommitDB()

    ok, message = force_password_change_target(db, 1, 2, "127.0.0.1")

    assert ok is False
    assert message
    assert calls, "audit write must have been attempted"
    assert db.updated is False, "UPDATE ran despite a failed audit write"


def test_transactional_audit_failure_rolls_back(monkeypatch, audit_result):
    calls = audit_result(False)
    db = _TransactionalDB()

    ok, message = force_password_change_target(db, 1, 2, "127.0.0.1")

    assert ok is False
    assert message
    assert calls, "audit write must have been attempted"
    assert db.began is True
    assert db.rolled_back is True, "failed audit must roll the mutation back"
    assert db.committed is False, "a rolled-back mutation must not be committed"


def test_audit_success_still_allows_mutation(audit_result):
    """Non-vacuous control: a successful audit write keeps the happy path."""
    audit_result(True)
    db = _TransactionalDB()

    ok, message = force_password_change_target(db, 1, 2, "127.0.0.1")

    assert ok is True, message
    assert db.updated is True
    assert db.committed is True
    assert db.rolled_back is False
