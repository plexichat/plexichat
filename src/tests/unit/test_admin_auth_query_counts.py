"""Regression tests for admin self-service auth DB round trips.

The 2FA-disable and backup-code-regeneration hot paths used to read the same
``admin_users`` row twice: once to verify the current password and again to
fetch OTP columns / ``totp_enabled`` that were already present on that row.
``get_security_status`` did the same for ``backup_codes_hash``. These tests
count the reads each handler performs so a second round trip cannot creep
back in. Argon2/TOTP work is stubbed out — the tests are about query shape,
not crypto (the crypto work factors are deliberately left untouched).
"""

from unittest.mock import patch

from src.core.admin.auth.composer import AdminAuth


class _Cursor:
    def __init__(self, rowcount=1):
        self.rowcount = rowcount


class _CountingDB:
    """Records the normalized SQL of every fetch, so reads can be counted."""

    def __init__(self, row):
        self.row = row
        self.fetch_calls = []
        self.exec_calls = []

    def fetch_one(self, query, params=None):
        self.fetch_calls.append(" ".join(query.split()))
        return self.row

    def fetch_all(self, query, params=None):
        self.fetch_calls.append(" ".join(query.split()))
        return []

    def execute(self, query, params=None):
        self.exec_calls.append(" ".join(query.split()))
        return _Cursor(1)


def _admin_row(**overrides):
    row = {
        "username": "admin",
        "email": "admin@example.com",
        "password_hash": "argon-hash",
        "created_at": 1,
        "last_login": 2,
        "totp_enabled": 1,
        "must_setup_otp": 0,
        "backup_codes": None,
        "totp_secret": "BASE32SECRET",
        "totp_secret_encrypted": None,
        "backup_codes_hash": None,
    }
    row.update(overrides)
    return row


def _admin_reads(db):
    return [q for q in db.fetch_calls if "FROM admin_users" in q]


def test_disable_otp_reads_admin_row_once():
    db = _CountingDB(_admin_row())
    auth = AdminAuth(db)
    with (
        patch("src.utils.encryption.verify_password", return_value=True),
        patch(
            "src.core.admin.auth.helpers._check_otp_attempt_limit",
            return_value=True,
        ),
        patch("src.core.auth.totp.hash_backup_codes", return_value=["h"]),
        patch("src.core.admin.auth.otp_setup_mixin.pyotp.TOTP") as totp,
    ):
        totp.return_value.verify.return_value = True
        ok, _msg = auth.disable_otp(1, "current", "123456")

    assert ok is True
    assert len(_admin_reads(db)) == 1, db.fetch_calls


def test_regenerate_backup_codes_reads_admin_row_once():
    db = _CountingDB(_admin_row())
    auth = AdminAuth(db)
    with (
        patch("src.utils.encryption.verify_password", return_value=True),
        patch("src.core.auth.totp.hash_backup_codes", return_value=["h"]),
    ):
        ok, codes, _msg = auth.regenerate_backup_codes(1, "current")

    assert ok is True
    assert codes
    assert len(_admin_reads(db)) == 1, db.fetch_calls


def test_begin_otp_setup_reads_admin_row_once():
    db = _CountingDB(_admin_row())
    auth = AdminAuth(db)
    with (
        patch("src.utils.encryption.verify_password", return_value=True),
        patch("src.utils.encryption.encrypt_data", return_value="encrypted"),
        patch(
            "src.core.admin.auth.otp_setup_mixin._create_otp_challenge",
            return_value="challenge",
        ),
        patch("src.core.admin.auth.otp_setup_mixin.pyotp") as pyotp_mod,
    ):
        pyotp_mod.random_base32.return_value = "BASE32SECRET"
        pyotp_mod.TOTP.return_value.provisioning_uri.return_value = "otpauth://x"
        result = auth.begin_otp_setup(1, "current")

    assert result.success is True
    assert len(_admin_reads(db)) == 1, db.fetch_calls


def test_change_password_reads_admin_row_once():
    db = _CountingDB(_admin_row())
    auth = AdminAuth(db)
    with (
        patch("utils.config.get", return_value={}),
        patch("src.utils.encryption.verify_password", return_value=True),
        patch("src.utils.encryption.hash_password", return_value="new-hash"),
        patch(
            "src.core.admin.auth.password_mixin._sync_password_to_auth_users"
        ) as sync,
    ):
        ok, _msg = auth.change_password(1, "current", "NewValid123!")

    assert ok is True
    sync.assert_called_once()
    assert len(_admin_reads(db)) == 1, db.fetch_calls


def test_security_status_reads_admin_row_once():
    db = _CountingDB(_admin_row(backup_codes_hash='["h1", "h2"]'))
    auth = AdminAuth(db)
    with patch("utils.config.get", return_value={}):
        status = auth.get_security_status(1)

    assert status is not None
    assert status.backup_codes_remaining == 2
    assert len(_admin_reads(db)) == 1, db.fetch_calls
