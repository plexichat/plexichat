"""Regression: the in-process 2FA guessing limiter must be a bounded LRU.

``TwoFactorMixin._2fa_verify_attempts_local`` used to be a bare ``dict`` keyed
by user id that grew for the life of the process: every account that typed a
wrong 2FA code left a timestamp bucket behind for ever. It is now a bounded
LRU with an entry cap, an approximate byte cap and an idle TTL.

The security invariants are the important part. This dict *is* the TOTP
brute-force limiter when Valkey is unavailable, so eviction must never:

* reset the budget of an account that is actively guessing (the bucket with
  the most live attempts must be pinned against eviction), or
* shorten the configured limiter window via idle reclamation.

Run with: pytest -n 0 src/tests/auth/test_2fa_attempt_limiter_bounds.py
"""

import time

import pytest

from src.core.auth.exceptions import TwoFactorInvalidError
from src.core.auth.managers import twofactor as tf
from src.core.auth.managers.twofactor import TwoFactorMixin


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_valkey_and_clean_store(monkeypatch):
    """Force the in-process fallback path and start from an empty store."""
    # The limiter imports these lazily from the valkey_client module.
    import src.core.database.valkey_client as vc

    monkeypatch.setattr(vc, "get_client", lambda: None)
    monkeypatch.setattr(vc, "is_available", lambda: False)
    tf.clear_twofa_attempts()
    yield
    tf.clear_twofa_attempts()


def _check(user_id, max_attempts=5, window=300):
    TwoFactorMixin._check_and_record_2fa_attempt(
        None, user_id, max_attempts=max_attempts, window_seconds=window
    )


# --- bounds -----------------------------------------------------------------


def test_attempt_store_is_bounded_by_entry_cap(monkeypatch):
    monkeypatch.setattr(tf, "_TWOFA_ATTEMPTS_MAX_ENTRIES", 8)
    for uid in range(500):
        _check(uid)

    assert len(tf._TWOFA_ATTEMPTS) <= 8
    assert len(tf._TWOFA_ATTEMPTS_META) == len(tf._TWOFA_ATTEMPTS)


def test_attempt_store_is_bounded_by_byte_cap(monkeypatch):
    # A tiny byte cap forces byte-based eviction long before the entry cap.
    monkeypatch.setattr(tf, "_TWOFA_ATTEMPTS_MAX_ENTRIES", 10_000)
    monkeypatch.setattr(tf, "_TWOFA_ATTEMPTS_MAX_BYTES", 4096)
    for uid in range(1000):
        _check(uid)

    assert tf._TWOFA_ATTEMPTS_BYTES <= 4096
    assert len(tf._TWOFA_ATTEMPTS) <= 10_000


def test_idle_buckets_reclaimed_after_ttl(monkeypatch):
    monkeypatch.setattr(tf, "_TWOFA_ATTEMPTS_IDLE_TTL_SECONDS", 2)
    _check(1)
    assert 1 in tf._TWOFA_ATTEMPTS

    # Age the only bucket past its idle deadline without touching it.
    meta = list(tf._TWOFA_ATTEMPTS_META[1])
    meta[1] = time.monotonic() - 1
    tf._TWOFA_ATTEMPTS_META[1] = tuple(meta)

    _check(2)
    assert 1 not in tf._TWOFA_ATTEMPTS
    assert 2 in tf._TWOFA_ATTEMPTS


# --- no weakening of the security guard -------------------------------------


def test_rate_limit_still_enforced_after_many_distinct_users(monkeypatch):
    monkeypatch.setattr(tf, "_TWOFA_ATTEMPTS_MAX_ENTRIES", 4)
    # Flood distinct ids to force several eviction cycles.
    for uid in range(100):
        _check(uid)

    # A single account still gets exactly max_attempts free tries.
    for _ in range(3):
        _check(4242, max_attempts=3)
    with pytest.raises(TwoFactorInvalidError):
        _check(4242, max_attempts=3)


def test_active_guessing_bucket_never_evicted(monkeypatch):
    monkeypatch.setattr(tf, "_TWOFA_ATTEMPTS_MAX_ENTRIES", 4)
    # Fill the registry with cold one-attempt buckets.
    for uid in range(4):
        _check(uid)

    # "Attacker" burns the full 5-attempt budget, becoming the hottest bucket.
    for _ in range(5):
        _check(999, max_attempts=5)

    # More cold traffic forces evictions; the hot bucket must survive with
    # its full count, so the next guess is still refused.
    for uid in range(100, 200):
        _check(uid)

    assert 999 in tf._TWOFA_ATTEMPTS
    assert len(tf._TWOFA_ATTEMPTS[999]) == 5
    with pytest.raises(TwoFactorInvalidError):
        _check(999, max_attempts=5)


def test_idle_reclamation_does_not_shorten_configured_window(monkeypatch):
    # A caller-supplied window longer than the idle floor must be honoured by
    # the bucket expiry (expiry = last_seen + max(window, idle floor)).
    monkeypatch.setattr(tf, "_TWOFA_ATTEMPTS_IDLE_TTL_SECONDS", 10)
    _check(7, window=3600)
    expiry = tf._TWOFA_ATTEMPTS_META[7][1]
    assert expiry - time.monotonic() > 3000


def test_eviction_never_drops_the_bucket_being_updated(monkeypatch):
    monkeypatch.setattr(tf, "_TWOFA_ATTEMPTS_MAX_ENTRIES", 2)
    _check(1)
    _check(2)
    # Updating 1 again must leave 1 present as the most-recently-used entry.
    _check(1)
    assert 1 in tf._TWOFA_ATTEMPTS
    assert list(tf._TWOFA_ATTEMPTS)[-1] == 1
    assert len(tf._TWOFA_ATTEMPTS) <= 2
