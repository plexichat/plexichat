"""Forcing an admin password change must revoke that admin's live sessions.

``force_password_change`` sets ``admin_users.force_password_change = 1``, but
that flag is only consulted at *login* (``check_password_change_required``) --
no admin route gates on it.  Before this fix the target's already-issued admin
bearer tokens stayed valid in ``admin_sessions`` until natural expiry, so an
operator forcing a rotation (typically because the account is suspected
compromised) did not actually stop that account from performing admin actions
with a token minted before the revocation.  Every other password-change path
(``change_password`` / ``set_password`` / ``change_admin_password``) already
deletes the account's admin sessions; this path was the outlier.

The DB double below models the session rows directly, so the assertions are on
the resulting state (target sessions gone, unrelated sessions untouched) rather
than merely on the SQL text.  The test drives the real public-API wrapper the
admin route calls, with only the hierarchy/audit side effects stubbed.
"""

import pytest

import src.core.admin as admin_core

pytestmark = [pytest.mark.security, pytest.mark.unit]


class _MiniDB:
    """Tiny stateful double modelling the tables these helpers touch."""

    def __init__(self, sessions):
        # sessions: list of (admin_id, token) currently in admin_sessions
        self.sessions = list(sessions)
        # statements records every attempted write (including rolled-back ones).
        self.statements = []
        self._in_txn = False
        self._pending = []
        self.committed = False
        self.rolled_back = False

    def fetch_one(self, sql, params=()):
        normalized = " ".join(sql.split())
        if "FROM admin_users" in normalized:
            return {"id": params[0]}  # target exists
        return None

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        params = tuple(params)
        self.statements.append((normalized, params))
        if self._in_txn:
            self._pending.append((normalized, params))
        else:
            self._apply(normalized, params)

    def _apply(self, normalized, params):
        if normalized.startswith("UPDATE admin_users SET force_password_change"):
            # params[0] is WHERE id = ? (the target); the flag literal is in SQL.
            self.force_updated_admin = params[0]
        elif normalized.startswith("DELETE FROM admin_sessions WHERE admin_id"):
            target = params[0]
            self.sessions = [row for row in self.sessions if row[0] != target]

    def begin_transaction(self):
        self._in_txn = True
        self._pending = []

    def commit(self):
        self.committed = True
        for statement in self._pending:
            self._apply(*statement)
        self._pending = []
        self._in_txn = False

    def rollback(self):
        self.rolled_back = True
        self._pending = []
        self._in_txn = False


@pytest.fixture
def _harness(monkeypatch):
    """Wire the public API to the in-memory DB; stub hierarchy and audit."""
    db = _MiniDB(
        sessions=[
            (2, "target-token-a"),
            (2, "target-token-b"),
            (3, "unrelated-admin-token"),
        ]
    )
    monkeypatch.setattr(admin_core, "_get_db", lambda: db)
    monkeypatch.setattr(
        "src.core.admin.permissions.can_manage_admin",
        lambda db, actor, target: True,
        raising=False,
    )
    audit_calls = []

    def _audit(*args, **kwargs):
        audit_calls.append((args, kwargs))
        return True

    monkeypatch.setattr(
        "src.core.admin.permissions.log_admin_action", _audit, raising=False
    )
    return db, audit_calls


def _index_of(db, prefix):
    return next(
        i for i, (sql, _params) in enumerate(db.statements) if sql.startswith(prefix)
    )


def test_force_password_change_revokes_target_admin_sessions(_harness):
    db, audit_calls = _harness

    ok, message = admin_core.force_password_change(1, 2, "10.0.0.1")

    assert ok is True, message
    assert getattr(db, "force_updated_admin", None) == 2
    # The target cannot keep using any token minted before the revocation.
    assert db.sessions == [(3, "unrelated-admin-token")]
    # Revocation rides the same write as the flag flip, after it -- not before.
    update_idx = _index_of(db, "UPDATE admin_users SET force_password_change")
    delete_idx = _index_of(db, "DELETE FROM admin_sessions WHERE admin_id")
    assert delete_idx > update_idx
    assert db.statements[delete_idx][1] == (2,)
    assert audit_calls, "the privileged mutation must still be audited"
    assert db.committed is True and db.rolled_back is False


def test_failed_mutation_leaves_existing_admin_sessions_intact(monkeypatch):
    """Fail closed: a refused mutation must not silently revoke sessions."""
    db = _MiniDB(sessions=[(2, "target-token-a")])
    monkeypatch.setattr(admin_core, "_get_db", lambda: db)
    monkeypatch.setattr(
        "src.core.admin.permissions.can_manage_admin",
        lambda db, actor, target: True,
        raising=False,
    )
    # Audit failure makes the service layer refuse the mutation entirely.
    monkeypatch.setattr(
        "src.core.admin.permissions.log_admin_action",
        lambda *args, **kwargs: False,
        raising=False,
    )

    ok, _message = admin_core.force_password_change(1, 2, "10.0.0.1")

    assert ok is False
    assert db.sessions == [(2, "target-token-a")]
    assert all(
        not sql.startswith("DELETE FROM admin_sessions")
        for sql, _params in db.statements
    )
