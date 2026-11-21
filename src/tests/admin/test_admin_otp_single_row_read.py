"""Regression: admin OTP verification reads admin_users exactly once.

``verify_otp`` and ``verify_otp_setup`` used to issue two SELECTs against the
same ``admin_users`` row per verification: one for the TOTP secret (plus backup
codes on the login path) and a second for the ``otp_last_used_code`` reuse
guard. Both sets of columns live on the row we already pay to read, so the
second round trip was pure overhead on the admin login hot path.

This pins one row read per verification while confirming the single-use guard
is untouched: the claim is still an atomic conditional UPDATE keyed on the
presented code, and a previously used code is still rejected before any claim.
"""

from unittest.mock import MagicMock

import pytest

from src.core.admin.auth import otp_mixin as om

CODE = "123456"


class _CountingDB:
    """Minimal ``admin_users`` stand-in that records every read and write."""

    def __init__(self, last_code=None, cas_rowcount=1):
        self.queries = []
        self.executed = []
        self.last_code = last_code
        self._cas_rowcount = cas_rowcount

    def fetch_one(self, sql, params=None):
        self.queries.append(sql)
        return {
            "totp_secret": "SECRET",
            "totp_secret_encrypted": None,
            "backup_codes": None,
            "backup_codes_hash": None,
            "otp_last_used_code": self.last_code,
            "otp_last_used_at": 1 if self.last_code else None,
        }

    def execute(self, sql, params=None):
        self.executed.append(sql)
        return MagicMock(rowcount=self._cas_rowcount)


@pytest.fixture
def mixin(monkeypatch):
    monkeypatch.setattr(om, "_validate_otp_challenge", lambda *a, **k: True)
    monkeypatch.setattr(om, "_consume_otp_challenge", lambda *a, **k: None)
    monkeypatch.setattr(om, "_check_otp_attempt_limit", lambda *a, **k: True)
    monkeypatch.setattr(om, "_check_admin_otp_budget", lambda admin_id: True)
    fake_totp = MagicMock()
    fake_totp.verify.return_value = True
    monkeypatch.setattr(om.pyotp, "TOTP", lambda secret: fake_totp)

    obj = object.__new__(om.OTPMixin)
    obj.create_session = MagicMock(return_value="session-token")
    obj.check_password_change_required = MagicMock(return_value=False)
    return obj


@pytest.mark.parametrize("method", ["verify_otp", "verify_otp_setup"])
def test_one_admin_row_read_per_verification(mixin, method):
    """Both verification paths fetch the admin row once (was twice)."""
    mixin._db = _CountingDB()

    getattr(mixin, method)(1, CODE, "challenge")

    assert len(mixin._db.queries) == 1, mixin._db.queries
    assert "otp_last_used_code" in mixin._db.queries[0]


@pytest.mark.parametrize("method", ["verify_otp", "verify_otp_setup"])
def test_single_use_cas_still_guards_the_claim(mixin, method):
    """A lost CAS must still refuse to mint a session."""
    mixin._db = _CountingDB(cas_rowcount=0)

    result = getattr(mixin, method)(1, CODE, "challenge")

    assert result.success is False
    mixin.create_session.assert_not_called()
    assert any(
        "otp_last_used_code IS NULL OR otp_last_used_code <> ?" in sql
        for sql in mixin._db.executed
    ), "single-use CAS update went missing"


@pytest.mark.parametrize("method", ["verify_otp", "verify_otp_setup"])
def test_reused_code_still_fails_closed(mixin, method):
    """A code recorded as last-used is rejected before any claim write."""
    mixin._db = _CountingDB(last_code=CODE)

    result = getattr(mixin, method)(1, CODE, "challenge")

    assert result.success is False
    assert "already used" in (result.error or "")
    assert not mixin._db.executed, "reuse guard must reject before claiming"
