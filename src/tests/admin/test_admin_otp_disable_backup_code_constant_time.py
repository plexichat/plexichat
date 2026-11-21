"""Regression: admin OTP-disable backup-code checks must be constant-time.

``verify_otp`` was hardened to use ``constant_time_compare`` for backup codes,
but ``disable_otp`` kept the original short-circuit ``==`` / ``in`` checks.
A timing observer could therefore recover a stored backup code via the disable
flow even after the login flow was fixed, and then disable 2FA with it. Pin
that both branches of ``disable_otp`` use the shared constant-time helper while
still accepting valid codes and rejecting invalid ones.
"""

import hashlib
import json
from unittest.mock import MagicMock

import pytest

from src.core.admin.auth import otp_setup_mixin as osm
from src.core.admin.auth import helpers as admin_helpers


CODE = "DEADBEEF"


def _digest(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").lower().encode()).hexdigest()


def _db_for(backup_hash=None, backup_plaintext=None):
    db = MagicMock()
    db.execute.return_value.rowcount = 1
    row = {
        "username": "admin",
        "totp_enabled": True,
        "must_setup_otp": False,
        "backup_codes": backup_plaintext,
        "backup_codes_hash": backup_hash,
        "totp_secret": "SECRET",
        "totp_secret_encrypted": None,
        "password_hash": "hash",
    }
    db.fetch_one.return_value = row
    return db


@pytest.fixture
def mixin(monkeypatch):
    # Password check always passes; TOTP always fails so the backup-code branch
    # is the one under test. Both rate-limit gates always allow the attempt.
    row_seed = {}

    def _verify_pw(db, admin_id, password):
        return True, row_seed.get("row"), ""

    monkeypatch.setattr(osm, "_verify_admin_password", _verify_pw)
    monkeypatch.setattr(osm, "_check_admin_otp_budget", lambda *a, **k: True)
    monkeypatch.setattr(admin_helpers, "_check_otp_attempt_limit", lambda *a, **k: True)
    monkeypatch.setattr("src.utils.encryption.verify_password", lambda *a, **k: False)
    fake_totp = MagicMock()
    fake_totp.verify.return_value = False
    monkeypatch.setattr(osm.pyotp, "TOTP", lambda secret: fake_totp)

    obj = object.__new__(osm.OTPSetupMixin)
    obj._row_seed = row_seed
    return obj


def _set_db(mixin, backup_hash=None, backup_plaintext=None):
    mixin._db = _db_for(backup_hash=backup_hash, backup_plaintext=backup_plaintext)
    mixin._row_seed["row"] = mixin._db.fetch_one.return_value


def _spy_comparator(monkeypatch):
    real = osm.constant_time_compare
    calls = []

    def spy(left, right):
        calls.append((left, right))
        return real(left, right)

    monkeypatch.setattr(osm, "constant_time_compare", spy)
    return calls


def test_disable_hashed_backup_code_uses_constant_time_comparator(mixin, monkeypatch):
    calls = _spy_comparator(monkeypatch)
    stored = _digest(CODE)
    _set_db(mixin, backup_hash=json.dumps([stored]))

    ok, message = mixin.disable_otp(1, "password", CODE)

    assert ok is True, message
    assert calls, "hashed backup-code comparison did not use constant_time_compare"
    assert calls[0] == (stored, stored)


def test_disable_legacy_plaintext_backup_code_scans_all_constant_time(
    mixin, monkeypatch
):
    calls = _spy_comparator(monkeypatch)
    _set_db(mixin, backup_plaintext=f"AAAAAAAA,{CODE},CCCCCCCC")

    ok, message = mixin.disable_otp(1, "password", f"{CODE[:4]}-{CODE[4:]}")

    assert ok is True, message
    assert len(calls) == 3, "whole list must be scanned so the index cannot be timed"
    assert any(right == CODE for _, right in calls)


def test_disable_wrong_backup_code_still_rejected_and_compared(mixin, monkeypatch):
    calls = _spy_comparator(monkeypatch)
    _set_db(mixin, backup_plaintext=f"AAAAAAAA,{CODE},CCCCCCCC")

    ok, _ = mixin.disable_otp(1, "password", "WRONGCODE")

    assert ok is False
    assert len(calls) == 3, "wrong code must still be compared in constant time"
