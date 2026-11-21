"""Regression: OTP guess budgets must follow the account, not the challenge.

A correct password mints a fresh single-use challenge and resets the IP login
bucket, so a per-challenge budget alone let an attacker who only has the
password mint unlimited challenges and try one TOTP code against each. All OTP
verification entry points now share an account-scoped budget.
"""

from unittest.mock import MagicMock

import pytest

from src.core.admin.auth import otp_mixin as om
from src.core.admin.auth import otp_setup_mixin as osm


class _FakeRateLimits:
    """Deterministic sliding-window counter backed by a plain dict."""

    def __init__(self):
        self.counters = {}
        self.now = 1000.0

    def check(self, key, limit, window):
        fresh = [ts for ts in self.counters.get(key, []) if ts > self.now - window]
        if len(fresh) >= limit:
            self.counters[key] = fresh
            return False, 0
        fresh.append(self.now)
        self.counters[key] = fresh
        return True, limit - len(fresh)


@pytest.fixture
def fake_limits(monkeypatch):
    limits = _FakeRateLimits()
    monkeypatch.setattr("src.core.database.check_rate_limit", limits.check)
    return limits


def _admin_db():
    db = MagicMock()

    def fetch_one(sql, params=None):
        # verify_otp/verify_otp_setup read the whole admin_users row in
        # one query; the fake must carry the secret, backup codes and
        # the reuse guard together on that single row.
        return {
            "totp_secret": "SECRET",
            "totp_secret_encrypted": None,
            "backup_codes": None,
            "backup_codes_hash": None,
            "otp_last_used_code": None,
            "otp_last_used_at": None,
        }

    db.fetch_one.side_effect = fetch_one
    return db


@pytest.fixture
def otp_mixin(monkeypatch):
    monkeypatch.setattr(om, "_validate_otp_challenge", lambda *a, **k: True)
    monkeypatch.setattr(om, "_consume_otp_challenge", lambda *a, **k: None)
    fake_totp = MagicMock()
    fake_totp.verify.return_value = False
    monkeypatch.setattr(om.pyotp, "TOTP", lambda secret: fake_totp)

    mixin = object.__new__(om.OTPMixin)
    mixin._db = _admin_db()
    mixin.create_session = MagicMock()
    mixin.check_password_change_required = MagicMock(return_value=False)
    return mixin


def test_fresh_challenges_cannot_reset_the_otp_guess_budget(otp_mixin, fake_limits):
    budget = om.ADMIN_OTP_MAX_ATTEMPTS

    # Each call uses a different, otherwise-valid challenge token. The old
    # per-challenge limiter allowed a fresh five-attempt window every time.
    for attempt in range(budget):
        result = otp_mixin.verify_otp(1, f"{attempt:06d}", f"challenge-{attempt}")
        assert result.success is False
        assert result.error == "Invalid OTP code"

    blocked = otp_mixin.verify_otp(1, "999999", "brand-new-challenge")
    assert blocked.success is False
    assert blocked.error == "Too many OTP attempts"
    otp_mixin.create_session.assert_not_called()
    assert "admin_otp:admin_otp_account:1" in fake_limits.counters


def test_setup_and_login_flows_share_one_account_budget(otp_mixin, fake_limits):
    # Spend the whole budget across both flows. Alternating between them must
    # not grant extra guesses even when each challenge is brand new.
    login_attempts = 2
    for attempt in range(login_attempts):
        assert (
            otp_mixin.verify_otp(1, f"1{attempt:05d}", f"login-{attempt}").error
            == "Invalid OTP code"
        )
    for attempt in range(om.ADMIN_OTP_MAX_ATTEMPTS - login_attempts):
        assert (
            otp_mixin.verify_otp_setup(1, f"2{attempt:05d}", f"setup-{attempt}").error
            == "Invalid OTP code"
        )

    blocked = otp_mixin.verify_otp_setup(1, "999999", "setup-after-budget")
    assert blocked.success is False
    assert blocked.error == "Too many OTP attempts"


def test_disable_flow_uses_the_shared_account_budget(monkeypatch, fake_limits):
    """disable_otp must not be an alternate route around the same budget."""
    monkeypatch.setattr(
        osm,
        "_verify_admin_password",
        lambda *a, **k: (
            True,
            {
                "username": "admin",
                "totp_enabled": 1,
                "totp_secret": "SECRET",
                "totp_secret_encrypted": None,
                "backup_codes": None,
                "backup_codes_hash": None,
            },
            "",
        ),
    )
    monkeypatch.setattr(
        osm.pyotp, "TOTP", lambda secret: MagicMock(verify=lambda *a, **k: False)
    )
    mixin = object.__new__(osm.OTPSetupMixin)
    mixin._db = MagicMock()

    for attempt in range(om.ADMIN_OTP_MAX_ATTEMPTS):
        ok, message = osm.OTPSetupMixin.disable_otp(
            mixin, 1, "password", f"{attempt:06d}"
        )
        assert ok is False
        assert message == "Invalid OTP or backup code"

    ok, message = osm.OTPSetupMixin.disable_otp(mixin, 1, "password", "999999")
    assert ok is False
    assert message == "Too many OTP attempts"
