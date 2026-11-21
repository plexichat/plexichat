"""Admin OTP must reject Unicode digit homoglyphs that pyotp NFKC-normalizes.

pyotp's ``TOTP.verify`` normalises its input with NFKC before comparing, so a
fullwidth spelling of a 6-digit code (e.g. "１２３４５６") verifies for the same
secret as the ASCII spelling. The admin OTP replay guard stores and compares
the *raw* presented code, so after "123456" is consumed, resubmitting the very
same one-time code as "１２３４５６" bypasses both the pre-check and the
conditional single-use UPDATE and mints a second session.

The user-facing boundary (``src.core.auth.totp.verify_totp_code``) already
pins codes to ASCII digits; the admin hot path must do the same.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pyotp

from src.core.admin.auth import otp_mixin as om
from src.tests._conftest.totp_clock import frozen_totp_time


def _fullwidth_digits(code: str) -> str:
    return "".join(chr(ord(ch) + 0xFF10 - 0x30) for ch in code)


class _ReplayDB:
    """DB double that enforces the real single-use CAS on otp_last_used_code."""

    def __init__(self, secret: str):
        self.row = {
            "totp_secret": secret,
            "totp_secret_encrypted": None,
            "backup_codes": None,
            "backup_codes_hash": None,
            "otp_last_used_code": None,
            "otp_last_used_at": None,
        }
        self.claim_sql = []

    def fetch_one(self, sql, params=None):
        return dict(self.row)

    def execute(self, sql, params=None):
        if "otp_last_used_code = ?" in sql:
            self.claim_sql.append((sql, tuple(params)))
            presented = params[0]
            previous = self.row["otp_last_used_code"]
            if previous is None or previous != presented:
                self.row["otp_last_used_code"] = presented
                return SimpleNamespace(rowcount=1)
            return SimpleNamespace(rowcount=0)
        return SimpleNamespace(rowcount=1)


def _mixin(monkeypatch, secret):
    monkeypatch.setattr(om, "_validate_otp_challenge", lambda *a, **k: True)
    monkeypatch.setattr(om, "_consume_otp_challenge", lambda *a, **k: None)
    monkeypatch.setattr(om, "_check_otp_attempt_limit", lambda *a, **k: True)
    obj = object.__new__(om.OTPMixin)
    obj._db = _ReplayDB(secret)
    obj.create_session = MagicMock(return_value="session-token")
    obj.check_password_change_required = MagicMock(return_value=False)
    return obj


def test_admin_verify_otp_rejects_homoglyph_replay(monkeypatch):
    secret = pyotp.random_base32()
    with frozen_totp_time():
        code = pyotp.TOTP(secret).now()
    homoglyph = _fullwidth_digits(code)
    # Sanity: the differential that makes this exploitable.
    with frozen_totp_time():
        assert pyotp.TOTP(secret).verify(homoglyph, valid_window=1) is True

    mixin = _mixin(monkeypatch, secret)
    with frozen_totp_time():
        first = mixin.verify_otp(7, code, "challenge-1")
        replay = mixin.verify_otp(7, homoglyph, "challenge-2")

    assert first.success is True
    # The one-time code is already burned; a homoglyph spelling must not
    # claim a fresh single-use slot and mint a second session.
    assert replay.success is False
    assert mixin.create_session.call_count == 1


def test_admin_verify_otp_setup_rejects_homoglyph_replay(monkeypatch):
    secret = pyotp.random_base32()
    with frozen_totp_time():
        code = pyotp.TOTP(secret).now()
    homoglyph = _fullwidth_digits(code)

    mixin = _mixin(monkeypatch, secret)
    with frozen_totp_time():
        first = mixin.verify_otp_setup(7, code, "challenge-1")
        replay = mixin.verify_otp_setup(7, homoglyph, "challenge-2")

    assert first.success is True
    assert replay.success is False
    assert mixin.create_session.call_count == 1
