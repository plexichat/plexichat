"""Regression: admin OTP challenge validation must be an atomic single-use gate.

``_validate_otp_challenge`` used to be a pure cache *read*: the one-time
challenge was only deleted later, by a separate ``_consume_otp_challenge``
call in the verify flow. That check-then-delete gap let two concurrent OTP
verifications present the same challenge, both pass validation, and both reach
the code check. Because the per-code CAS in ``OTPMixin.verify_otp`` only
rejects reuse of the *same* code, two different still-valid TOTP codes
(``valid_window=1`` admits three adjacent codes) both minted a session from a
single challenge.

These tests pin the single-use property at the gate itself: exactly one
concurrent validator may win, and a full end-to-end double spend is impossible.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from src.core.admin.auth import helpers as h
from src.core.admin.auth import otp_mixin as om


def _reset_claim_guard() -> None:
    guard = getattr(h, "_OTP_CHALLENGE_CLAIM_GUARD", None)
    if guard is not None:
        guard.clear()


@pytest.fixture(autouse=True)
def _no_shared_valkey(monkeypatch):
    """Force the process-local claim path so the test is deterministic."""
    import src.core.database.valkey_client as vc

    monkeypatch.setattr(vc, "get_client", lambda: None)
    monkeypatch.setattr(vc, "is_available", lambda: False)
    _reset_claim_guard()
    yield
    _reset_claim_guard()


def test_valkey_claim_is_a_single_set_nx(monkeypatch):
    """Cross-worker consumption is one atomic SET NX, not a Python TOCTOU."""
    import src.core.database.valkey_client as vc

    calls: list[tuple[str, int]] = []

    class AtomicClient:
        def set_nx(self, key, value, ttl=None):
            calls.append((key, ttl))
            return len(calls) == 1  # only the first caller wins

    monkeypatch.setattr(vc, "get_client", lambda: AtomicClient())
    monkeypatch.setattr(vc, "is_available", lambda: True)

    fingerprint = "a" * 64
    assert h._claim_otp_challenge_once(fingerprint, 120) is True
    assert h._claim_otp_challenge_once(fingerprint, 120) is False
    assert calls == [
        (f"admin_otp_claim:{fingerprint}", 120),
        (f"admin_otp_claim:{fingerprint}", 120),
    ]


def test_validation_of_one_challenge_has_exactly_one_winner():
    """Concurrent validations of one challenge must not all pass the gate."""
    token = h._create_otp_challenge(admin_id=7, is_setup=False)

    workers = 16
    barrier = threading.Barrier(workers)
    outcomes: list[bool] = []

    def race() -> None:
        barrier.wait()
        outcomes.append(h._validate_otp_challenge(token, 7, is_setup=False))

    threads = [threading.Thread(target=race) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(outcomes) == workers
    assert sum(outcomes) == 1, "exactly one concurrent consumer may win"


def test_concurrent_verify_otp_cannot_double_spend_one_challenge(monkeypatch):
    """Two different valid TOTP codes cannot both consume one challenge."""
    import src.core.database as database

    monkeypatch.setattr(database, "check_rate_limit", lambda *a, **k: (True, 5))

    token = h._create_otp_challenge(admin_id=7, is_setup=False)
    challenge_key = f"admin_otp_challenge:{h._otp_challenge_fingerprint(token)}"

    # Force both verifiers past the cache read *before* either can consume the
    # challenge, so the check-then-delete gap is deterministically exercised.
    read_barrier = threading.Barrier(2)
    real_cache_get = database.cache_get

    def gated_cache_get(key):
        value = real_cache_get(key)
        if key == challenge_key:
            read_barrier.wait(timeout=5)
        return value

    monkeypatch.setattr(database, "cache_get", gated_cache_get)

    class GuardedAdminDB:
        """Minimal admin_users store with a real conditional CAS."""

        def __init__(self) -> None:
            self.last_code = None
            self.lock = threading.Lock()

        def fetch_one(self, sql, params=None):
            return {
                "totp_secret": "SECRET",
                "totp_secret_encrypted": None,
                "backup_codes": None,
                "backup_codes_hash": None,
                "otp_last_used_code": self.last_code,
                "otp_last_used_at": None,
            }

        def execute(self, sql, params=None):
            cursor = MagicMock()
            if "otp_last_used_code" in sql and "UPDATE" in sql:
                with self.lock:
                    if self.last_code is None or self.last_code != params[0]:
                        self.last_code = params[0]
                        cursor.rowcount = 1
                    else:
                        cursor.rowcount = 0
            else:
                cursor.rowcount = 1
            return cursor

    db = GuardedAdminDB()
    mixin = object.__new__(om.OTPMixin)
    mixin._db = db
    mixin.create_session = lambda admin_id: "session"
    mixin.check_password_change_required = lambda admin_id: False

    # Any submitted code is valid (two adjacent TOTP windows are live at once).
    fake_totp = MagicMock()
    fake_totp.verify.return_value = True
    monkeypatch.setattr(om.pyotp, "TOTP", lambda secret: fake_totp)

    barrier = threading.Barrier(2)
    results = []

    def race(code: str) -> None:
        barrier.wait()
        results.append(om.OTPMixin.verify_otp(mixin, 7, code, token))

    threads = [
        threading.Thread(target=race, args=(code,)) for code in ("111111", "222222")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 2
    assert sum(1 for result in results if result.success) == 1, (
        "one challenge must not mint two sessions"
    )
