"""Regression: a peppered admin credential must be able to log in.

``authenticate_admin`` refuses credentials that are not Argon2, so a leaked
unsalted digest cannot become a login. Since passwords gained a pepper, a stored
credential is an envelope around the PHC string
(``$plexichat$pepper-v1$pid=v1$$argon2id$...``), and the guard compared the
*envelope* to ``$argon2…``. Every admin hash is peppered, so a valid password was
answered with ``Insecure password hash on file; contact operator`` and the admin
panel could not be used at all (found on the live a.1.0-166 deployment).
"""

from __future__ import annotations

import pytest

from src.core.admin.auth.auth_mixin import AuthenticationMixin, _stored_hash_is_argon2
from src.utils.encryption.pepper import parse_envelope

ARGON2 = "$argon2id$v=19$m=65536,t=3,p=2$c2FsdA$aGFzaA"
PEPPERED = f"$plexichat$pepper-v1$pid=v1${ARGON2}"


class _FakeDB:
    def __init__(self, row):
        self.row = row
        self.executed = []

    def fetch_one(self, sql, params=()):
        return self.row if "FROM admin_users" in sql else None

    def execute(self, sql, params=()):
        self.executed.append((sql, params))


def _manager(row):
    class Manager(AuthenticationMixin):
        def __init__(self, db):
            self._db = db

        def create_session(self, admin_id):
            return "session-token"

        def check_password_change_required(self, admin_id):
            return False

    return Manager(_FakeDB(row))


@pytest.mark.unit
class TestStoredHashGate:
    def test_unpeppered_argon2_is_accepted(self):
        assert _stored_hash_is_argon2(ARGON2) is True

    def test_peppered_argon2_is_accepted(self):
        assert parse_envelope(PEPPERED) is not None
        assert _stored_hash_is_argon2(PEPPERED) is True

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "plaintext",
            "INVALID_HASH_LOCKED",
            None,
            12345,
            "$sha256$deadbeef",
            "$plexichat$pepper-v1$pid=v1$not-argon2",
        ],
    )
    def test_non_argon2_credentials_are_refused(self, bad):
        assert _stored_hash_is_argon2(bad) is False


@pytest.mark.unit
class TestPepperedAdminLogin:
    def _prepare(self, monkeypatch, stored_hash, password_ok):
        import src.core.admin.auth.auth_mixin as mixin
        import src.core.database as database
        import src.utils.encryption as encryption

        monkeypatch.setattr(mixin, "_check_rate_limit", lambda *a, **k: (True, 0))
        monkeypatch.setattr(
            mixin.config,
            "get",
            lambda key, default=None: (
                {"require_otp": False, "rate_limit": {}}
                if key == "admin_ui"
                else default
            ),
        )
        monkeypatch.setattr(
            encryption,
            "verify_password",
            lambda password, h: password_ok and h == stored_hash,
        )
        monkeypatch.setattr(database, "reset_rate_limit", lambda *a, **k: None)

    def test_peppered_credential_logs_in(self, monkeypatch):
        self._prepare(monkeypatch, PEPPERED, password_ok=True)
        row = {
            "id": 7,
            "password_hash": PEPPERED,
            "totp_enabled": 0,
            "must_setup_otp": 0,
        }

        result = _manager(row).authenticate_admin(
            "admin", "correct-password", "127.0.0.1"
        )

        assert result.success is True, result.error
        assert result.token == "session-token"

    def test_non_argon2_credential_is_still_refused(self, monkeypatch):
        self._prepare(monkeypatch, "deadbeef", password_ok=True)
        row = {
            "id": 8,
            "password_hash": "deadbeef",
            "totp_enabled": 0,
            "must_setup_otp": 0,
        }

        result = _manager(row).authenticate_admin(
            "admin", "correct-password", "127.0.0.1"
        )

        assert result.success is False
        assert result.error == "Insecure password hash on file; contact operator"
        assert result.token is None
