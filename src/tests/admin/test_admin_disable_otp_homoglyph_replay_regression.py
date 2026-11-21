"""Admin OTP *disable* must reject Unicode digit homoglyphs that pyotp NFKC-normalizes.

pyotp's ``TOTP.verify`` NFKC-normalises its input before comparing, so a
fullwidth spelling of a 6-digit code (e.g. "１２３４５６") verifies for the same
secret as the ASCII spelling. The single-use replay guard on the admin
``otp_last_used_code`` column is a compare-and-set on the *raw* presented code.
After an ASCII code is consumed (e.g. by login), resubmitting that same one-time
code as a homoglyph verifies in pyotp but no longer matches the raw stored code,
so the CAS succeeds and OTP can be disabled with an already-used code.

``verify_otp``/``verify_otp_setup`` in ``otp_mixin.py`` pin codes to ASCII digits;
``disable_otp`` lives in the sibling ``otp_setup_mixin.py`` and still did not.
"""

from types import SimpleNamespace

import pyotp

from src.core.admin.auth import otp_setup_mixin as osm
from src.core.admin.auth import helpers as oh
from src.tests._conftest.totp_clock import frozen_totp_time


def _fullwidth_digits(code: str) -> str:
    return "".join(chr(ord(ch) + 0xFF10 - 0x30) for ch in code)


class _DisableDB:
    """DB double that enforces the real single-use CAS on otp_last_used_code."""

    def __init__(self, secret: str, last_used=None):
        self.row = {
            "username": "admin",
            "password_hash": "unused",
            "totp_enabled": 1,
            "must_setup_otp": 0,
            "backup_codes": None,
            "backup_codes_hash": None,
            "totp_secret": secret,
            "totp_secret_encrypted": None,
            "otp_last_used_code": last_used,
        }
        self.claim_sql = []
        self.disable_calls = 0

    def fetch_one(self, sql, params=None):
        return dict(self.row)

    def execute(self, sql, params=None):
        if "otp_last_used_code = ?" in sql:
            self.claim_sql.append(tuple(params))
            presented = params[0]
            previous = self.row["otp_last_used_code"]
            if previous is None or previous != presented:
                self.row["otp_last_used_code"] = presented
                return SimpleNamespace(rowcount=1)
            return SimpleNamespace(rowcount=0)
        if "totp_enabled = 0" in sql:
            self.disable_calls += 1
        return SimpleNamespace(rowcount=1)


def _mixin(monkeypatch, db, secret):
    row = dict(db.row)
    monkeypatch.setattr(osm, "_verify_admin_password", lambda *a, **k: (True, row, ""))
    monkeypatch.setattr(osm, "_check_admin_otp_budget", lambda *a, **k: True)
    monkeypatch.setattr(oh, "_check_otp_attempt_limit", lambda *a, **k: True)
    obj = object.__new__(osm.OTPSetupMixin)
    obj._db = db
    return obj


def test_disable_otp_rejects_homoglyph_replay(monkeypatch):
    """A homoglyph of an already-consumed code must not disable OTP."""
    secret = pyotp.random_base32()
    with frozen_totp_time():
        code = pyotp.TOTP(secret).now()
    homoglyph = _fullwidth_digits(code)

    # Sanity check the differential that makes this exploitable: pyotp accepts
    # the homoglyph as the very same numeric code.
    with frozen_totp_time():
        assert pyotp.TOTP(secret).verify(homoglyph, valid_window=1) is True

    # Simulate the state right after the admin used ``code`` to authenticate:
    # the raw ASCII value is recorded as the last-used (single-use) code.
    db = _DisableDB(secret, last_used=code)
    mixin = _mixin(monkeypatch, db, secret)

    with frozen_totp_time():
        ok, _msg = mixin.disable_otp(7, "current-password", homoglyph)

    assert ok is False
    # The single-use code was not re-claimed and OTP was not switched off.
    assert db.claim_sql == []
    assert db.row["otp_last_used_code"] == code
    assert db.disable_calls == 0


def test_disable_otp_accepts_fresh_canonical_code(monkeypatch):
    """Control: the ASCII gate must not block a legitimate fresh TOTP code."""
    secret = pyotp.random_base32()
    with frozen_totp_time():
        code = pyotp.TOTP(secret).now()

    db = _DisableDB(secret, last_used=None)
    mixin = _mixin(monkeypatch, db, secret)

    with frozen_totp_time():
        ok, _msg = mixin.disable_otp(7, "current-password", code)

    assert ok is True
    assert db.row["otp_last_used_code"] == code
    assert db.disable_calls == 1


def test_disable_otp_rejects_canonical_replay(monkeypatch):
    """Control: the raw-code CAS still blocks a plain ASCII replay."""
    secret = pyotp.random_base32()
    with frozen_totp_time():
        code = pyotp.TOTP(secret).now()

    db = _DisableDB(secret, last_used=code)
    mixin = _mixin(monkeypatch, db, secret)

    with frozen_totp_time():
        ok, _msg = mixin.disable_otp(7, "current-password", code)

    assert ok is False
    assert db.row["otp_last_used_code"] == code
    assert db.disable_calls == 0
