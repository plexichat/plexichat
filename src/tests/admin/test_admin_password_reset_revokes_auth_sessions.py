"""An admin password reset must revoke the account's main-app sessions.

The admin password-change paths write the same hash into ``auth_users`` (the
main-app credential store) but historically only deleted ``admin_sessions``.
Sessions in ``auth_sessions`` are verified solely by their `revoked` flag and
token hash -- there is no password-version check -- so a stolen session token
kept working after the credential was reset. Every user-facing password-change
path already revokes sessions; these tests pin that invariant for the admin
paths too.
"""

import pytest

from src.core.admin.auth import helpers
from src.core.admin.auth.password_mixin import PasswordMixin

pytestmark = [pytest.mark.security, pytest.mark.unit]


class _Cursor:
    rowcount = 1


class _FakeDB:
    def __init__(self, auth_user_id=7, username="admin"):
        self.auth_user_id = auth_user_id
        self.username = username
        self.executed = []

    def fetch_one(self, sql, params):
        if "FROM auth_users WHERE username" in sql:
            return {"id": self.auth_user_id}
        if "FROM admin_users WHERE id" in sql:
            return {"username": self.username}
        return None

    def execute(self, sql, params):
        self.executed.append((" ".join(sql.split()), tuple(params)))
        return _Cursor()


def _revocations(db):
    return [(sql, params) for sql, params in db.executed if "auth_sessions" in sql]


def test_sync_revokes_main_app_sessions(monkeypatch):
    monkeypatch.setattr("src.core.database.invalidate_pattern", lambda _pattern: None)
    db = _FakeDB()

    helpers._sync_password_to_auth_users(db, "admin", "newhash")

    revokes = _revocations(db)
    assert len(revokes) == 1, "the synced password must retire the old sessions"
    assert "revoked = 1" in revokes[0][0]
    assert revokes[0][1] == (7,), "revocation must target the synced user id"
    assert any(
        "UPDATE auth_users SET password_hash" in sql and params == ("newhash", 7)
        for sql, params in db.executed
    )


def test_sync_invalidates_token_verify_cache(monkeypatch):
    seen = []
    monkeypatch.setattr("src.core.database.invalidate_pattern", seen.append)

    helpers._sync_password_to_auth_users(_FakeDB(), "admin", "newhash")

    assert "token_verify:*" in seen, "verification cache must not replay a pre-reset ok"


def test_admin_set_password_revokes_target_sessions(monkeypatch):
    """Non-vacuous control: the real admin set_password path goes through it."""
    monkeypatch.setattr("src.core.database.invalidate_pattern", lambda _pattern: None)
    monkeypatch.setattr("src.utils.encryption.hash_password", lambda _pw: "hashed")
    db = _FakeDB()
    mixin = PasswordMixin()
    mixin._db = db

    ok, message = mixin.set_password(1, "Str0ng!Passw0rd2026!XyZ")

    assert ok is True, message
    revokes = _revocations(db)
    assert len(revokes) == 1
    assert revokes[0][1] == (7,)
    assert any("DELETE FROM admin_sessions" in sql for sql, _ in db.executed)
