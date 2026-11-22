"""Admin password login must bound online guessing per ACCOUNT, not per IP.

``authenticate_admin`` charges only the ``admin_login:{ip}`` bucket. A caller
that controls a botnet (or a trusted-proxy header chain) gets a fresh bucket
for every attempt, so it can try an unlimited number of passwords against the
one ``admin`` account without ever tripping a limiter. The admin OTP path
already carries an account-scoped budget (``admin_otp_account:``); the password
phase must have the same invariant.

Non-vacuity: with a fake limiter that keys only on the IP, eight wrong
passwords from eight different addresses are all answered "Invalid credentials"
today. Once the account budget exists the sixth guess is refused uniformly.
"""

from __future__ import annotations

import pytest

import src.core.admin.auth.auth_mixin as mixin
import src.core.database as database
import src.utils.encryption as encryption
from src.core.admin.auth.auth_mixin import AuthenticationMixin

_ARGON2 = "$argon2id$v=19$m=65536,t=3,p=2$c2FsdA$aGFzaA"


class _FakeDB:
    def __init__(self, row):
        self.row = row

    def fetch_one(self, sql, params=()):
        return self.row if "FROM admin_users" in sql else None

    def execute(self, sql, params=()):
        return None


def _manager():
    class Manager(AuthenticationMixin):
        def __init__(self, db):
            self._db = db

    return Manager(
        _FakeDB(
            {
                "id": 42,
                "password_hash": _ARGON2,
                "totp_enabled": 0,
                "must_setup_otp": 0,
            }
        )
    )


def _prepare(monkeypatch):
    monkeypatch.setattr(
        mixin.config,
        "get",
        lambda key, default=None: (
            {
                "require_otp": False,
                "rate_limit": {
                    "max_attempts": 5,
                    "window_seconds": 300,
                    "lockout_seconds": 900,
                },
            }
            if key == "admin_ui"
            else default
        ),
    )
    monkeypatch.setattr(encryption, "verify_password", lambda password, h: False)
    monkeypatch.setattr(database, "reset_rate_limit", lambda *a, **k: None)


@pytest.mark.security
@pytest.mark.unit
def test_wrong_passwords_from_distinct_ips_share_an_account_budget(monkeypatch):
    """Rotating the source IP must not reset the per-account password budget."""
    _prepare(monkeypatch)
    buckets: dict[str, int] = {}

    def fake_check_rate_limit(key, limit, window_seconds):
        count = buckets.get(key, 0) + 1
        buckets[key] = count
        return count <= limit, max(0, limit - count)

    monkeypatch.setattr(database, "check_rate_limit", fake_check_rate_limit)

    results = [
        _manager().authenticate_admin("admin", "wrong-password", f"198.51.100.{i + 1}")
        for i in range(8)
    ]

    # The per-IP buckets are all fresh (one attempt each), so only an
    # account-scoped budget can produce a throttle here.
    assert not any(r.rate_limited for r in results[:1]), "first guess must be allowed"
    assert any(r.rate_limited for r in results), (
        "eight wrong passwords against one admin from eight distinct IPs were "
        "all accepted - password guessing is bounded per IP only"
    )
    assert all(r.token is None for r in results)


@pytest.mark.security
@pytest.mark.unit
def test_account_password_budget_fails_closed_on_limiter_fault(monkeypatch):
    """A limiter backend fault must deny the login, not wave it through."""
    _prepare(monkeypatch)
    # The per-IP limiter stays healthy; only the account-scoped limiter faults.
    monkeypatch.setattr(mixin, "_check_rate_limit", lambda *a, **k: (True, 0))

    def faulting_check_rate_limit(key, limit, window_seconds):
        if "admin_login_account" in key:
            raise RuntimeError("limiter backend unavailable")
        return True, limit

    monkeypatch.setattr(database, "check_rate_limit", faulting_check_rate_limit)

    result = _manager().authenticate_admin("admin", "any-password", "203.0.113.9")

    assert result.success is False
    assert result.token is None
    assert result.rate_limited is True, "limiter fault must deny, not fail open"


@pytest.mark.security
@pytest.mark.unit
def test_successful_password_clears_the_account_budget(monkeypatch):
    """A legitimate login must not inherit stale wrong-password guesses."""
    _prepare(monkeypatch)
    buckets: dict[str, int] = {}
    resets: list[str] = []

    def fake_check_rate_limit(key, limit, window_seconds):
        count = buckets.get(key, 0) + 1
        buckets[key] = count
        return count <= limit, max(0, limit - count)

    def fake_reset(key):
        resets.append(key)
        buckets.pop(key, None)

    monkeypatch.setattr(database, "check_rate_limit", fake_check_rate_limit)
    monkeypatch.setattr(database, "reset_rate_limit", fake_reset)
    monkeypatch.setattr(
        encryption,
        "verify_password",
        lambda password, h: password == "the-right-password",
    )

    mgr = _manager()
    for _ in range(4):
        assert mgr.authenticate_admin("admin", "wrong", "198.51.100.1").token is None
    result = mgr.authenticate_admin("admin", "the-right-password", "198.51.100.1")

    assert result.success is True
    assert "admin_login_account:42" in resets, (
        "successful login left the account-scoped password budget charged"
    )
