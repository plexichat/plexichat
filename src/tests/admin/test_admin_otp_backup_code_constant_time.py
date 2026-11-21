"""Regression: admin OTP backup-code comparisons must be constant-time.

The admin login backup-code branch compared the submitted digest (and, in the
legacy plaintext branch, the code itself) with plain ``==`` / ``in``. Both
short-circuit on the first differing character, leaking the stored secret to a
timing observer. The hardened core TOTP path (``verify_backup_code``) already
uses ``hmac.compare_digest``; the admin path now routes through the shared
``constant_time_compare`` helper as well. These tests pin that behaviour while
confirming valid codes are still accepted and invalid ones still rejected.
"""

import hashlib
import json
from unittest.mock import MagicMock

import pytest

from src.core.admin.auth import otp_mixin as om


CODE = "DEADBEEF"


def _digest(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").lower().encode()).hexdigest()


def _db_for(backup_hash=None, backup_plaintext=None):
    db = MagicMock()
    db.execute.return_value.rowcount = 1

    def fetch_one(sql, params=None):
        # verify_otp/verify_otp_setup read the whole admin_users row in
        # one query; the fake must carry the secret, backup codes and
        # the reuse guard together on that single row.
        return {
            "totp_secret": "SECRET",
            "totp_secret_encrypted": None,
            "backup_codes": backup_plaintext,
            "backup_codes_hash": backup_hash,
            "otp_last_used_code": None,
            "otp_last_used_at": None,
        }

    db.fetch_one.side_effect = fetch_one
    return db


@pytest.fixture
def mixin(monkeypatch):
    # Budget + challenge checks always pass; TOTP always fails so the backup
    # code branch is the one under test; legacy password-hash fallback is off.
    monkeypatch.setattr(om, "_validate_otp_challenge", lambda *a, **k: True)
    monkeypatch.setattr(om, "_consume_otp_challenge", lambda *a, **k: None)
    monkeypatch.setattr(om, "_check_otp_attempt_limit", lambda *a, **k: True)
    monkeypatch.setattr("src.utils.encryption.verify_password", lambda *a, **k: False)
    fake_totp = MagicMock()
    fake_totp.verify.return_value = False
    monkeypatch.setattr(om.pyotp, "TOTP", lambda secret: fake_totp)

    obj = object.__new__(om.OTPMixin)
    obj.create_session = MagicMock(return_value="session-token")
    obj.check_password_change_required = MagicMock(return_value=False)
    return obj


def _spy_comparator(monkeypatch):
    """Wrap the module comparator so we can assert it is actually used."""
    real = om.constant_time_compare
    calls = []

    def spy(left, right):
        calls.append((left, right))
        return real(left, right)

    monkeypatch.setattr(om, "constant_time_compare", spy)
    return calls


def test_hashed_backup_code_uses_constant_time_comparator(mixin, monkeypatch):
    calls = _spy_comparator(monkeypatch)
    stored = _digest(CODE)
    mixin._db = _db_for(backup_hash=json.dumps([stored]))

    result = mixin.verify_otp(1, CODE, "challenge")

    assert result.success is True
    assert calls, "digest comparison did not use constant_time_compare"
    assert calls[0] == (stored, stored)


def test_hashed_wrong_code_still_rejected_and_compared(mixin, monkeypatch):
    calls = _spy_comparator(monkeypatch)
    stored = _digest(CODE)
    mixin._db = _db_for(backup_hash=json.dumps([stored]))

    result = mixin.verify_otp(1, "WRONGCODE", "challenge")

    assert result.success is False
    assert len(calls) == 1, "wrong digest must still be compared in constant time"
    assert calls[0] == (_digest("WRONGCODE"), stored)


def test_legacy_plaintext_backup_code_uses_constant_time_comparator(mixin, monkeypatch):
    calls = _spy_comparator(monkeypatch)
    mixin._db = _db_for(backup_plaintext=f"AAAAAAAA,{CODE},CCCCCCCC")

    result = mixin.verify_otp(1, f"{CODE[:4]}-{CODE[4:]}", "challenge")

    assert result.success is True
    assert calls, "plaintext code scan did not use constant_time_compare"
    # The whole list is scanned so the matching index cannot be timed.
    assert len(calls) == 3
    assert any(right == CODE for _, right in calls)
