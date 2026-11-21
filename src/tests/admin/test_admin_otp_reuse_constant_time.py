"""Regression: admin OTP reuse checks must compare codes in constant time.

``otp_last_used_code`` holds the previously consumed TOTP code. With
``valid_window=1`` that code is still a live authentication secret for the
remainder of its window, which is exactly why the reuse guard rejects it.
Both ``verify_otp`` and ``verify_otp_setup`` compared it with a plain ``==``,
which short-circuits on the first differing character and leaks a prefix
oracle to a timing observer. The helper now routes string comparisons through
the shared ``constant_time_compare`` primitive (as the backup-code path in the
same module already does), while preserving fail-closed behavior for None.
"""

from unittest.mock import MagicMock

import pytest

from src.core.admin.auth import otp_mixin as om


LAST_CODE = "123456"


def _db_with_last_code(last_code):
    db = MagicMock()
    db.execute.return_value.rowcount = 1

    def fetch_one(sql, params=None):
        # verify_otp/verify_otp_setup read the whole admin_users row in
        # one query; the fake must carry the secret, backup codes and
        # the reuse guard together on that single row.
        return {
            "totp_secret": "SECRET",
            "totp_secret_encrypted": None,
            "backup_codes": None,
            "backup_codes_hash": None,
            "otp_last_used_code": last_code,
            "otp_last_used_at": 1,
        }

    db.fetch_one.side_effect = fetch_one
    return db


@pytest.fixture
def mixin(monkeypatch):
    monkeypatch.setattr(om, "_validate_otp_challenge", lambda *a, **k: True)
    monkeypatch.setattr(om, "_consume_otp_challenge", lambda *a, **k: None)
    monkeypatch.setattr(om, "_check_otp_attempt_limit", lambda *a, **k: True)
    fake_totp = MagicMock()
    fake_totp.verify.return_value = True
    monkeypatch.setattr(om.pyotp, "TOTP", lambda secret: fake_totp)

    obj = object.__new__(om.OTPMixin)
    obj.create_session = MagicMock(return_value="session-token")
    obj.check_password_change_required = MagicMock(return_value=False)
    return obj


def _spy_comparator(monkeypatch):
    real = om.constant_time_compare
    calls = []

    def spy(left, right):
        calls.append((left, right))
        return real(left, right)

    monkeypatch.setattr(om, "constant_time_compare", spy)
    return calls


def test_verify_otp_reuse_uses_constant_time_comparator(mixin, monkeypatch):
    calls = _spy_comparator(monkeypatch)
    mixin._db = _db_with_last_code(LAST_CODE)

    result = mixin.verify_otp(1, LAST_CODE, "challenge")

    assert result.success is False
    assert "already used" in (result.error or "")
    assert calls, "last-used-code comparison skipped constant_time_compare"
    assert calls[0] == (LAST_CODE, LAST_CODE)


def test_verify_otp_setup_reuse_uses_constant_time_comparator(mixin, monkeypatch):
    calls = _spy_comparator(monkeypatch)
    mixin._db = _db_with_last_code(LAST_CODE)

    result = mixin.verify_otp_setup(1, LAST_CODE, "challenge")

    assert result.success is False
    assert "already used" in (result.error or "")
    assert calls, "last-used-code comparison skipped constant_time_compare"
    assert calls[0] == (LAST_CODE, LAST_CODE)


def test_different_code_still_compared_and_then_reaches_totp(mixin, monkeypatch):
    calls = _spy_comparator(monkeypatch)
    mixin._db = _db_with_last_code(LAST_CODE)

    result = mixin.verify_otp(1, "654321", "challenge")

    # A different code is not a reuse: the comparator ran, TOTP verified, and
    # the login succeeded (session minted).
    assert calls == [(LAST_CODE, "654321")]
    assert result.success is True
