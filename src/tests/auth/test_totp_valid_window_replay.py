"""TOTP one-time codes must consume the whole validity window.

``verify_totp_code`` accepts ``valid_window=1``, so three adjacent time-step
codes are cryptographically valid at any instant. The replay cache used to key
only the *presented code*, so two different still-valid codes each claimed a
fresh key and both passed, even for the same user and the same purpose. That
lets a single stolen/looked-over code window authorize more than one step-up
(e.g. two separate 2FA logins minted from two challenges).

The fix claims a window-level key (the current step and the next step) with the
same atomic, shared replay claim as the code itself, so exactly one code from a
validity window can ever be accepted for a given (user, purpose, actor).
"""

import time
from unittest.mock import patch

import pyotp

from src.core.auth import totp as totp_module
from src.core.auth.totp import TOTPReplayCache, verify_totp_code
from src.tests._conftest.totp_clock import frozen_totp_time


def _isolated_cache(monkeypatch):
    """Detach shared backends so the global replay cache is process-local."""
    cache = TOTPReplayCache(ttl_seconds=90)
    monkeypatch.setattr(totp_module, "_replay_cache", cache)
    return cache


def _adjacent_codes(secret):
    """Two distinct, cryptographically valid codes in the current window."""
    now_ts = int(time.time())
    totp = pyotp.TOTP(secret)
    current = totp.at(now_ts)
    adjacent = totp.at(now_ts + totp.interval)
    assert current != adjacent
    # Sanity: pyotp really admits both at once (valid_window=1 admits three).
    assert totp.verify(current, valid_window=1) is True
    assert totp.verify(adjacent, valid_window=1) is True
    return current, adjacent


def test_two_distinct_valid_adjacent_codes_cannot_both_be_used(monkeypatch):
    """One validity window yields exactly one accepted code per purpose.

    Before the fix the second call returned True: the adjacent code had a
    different replay-cache key and sailed through.
    """
    _isolated_cache(monkeypatch)
    secret = pyotp.random_base32()
    user_id = 987_654_321
    current, adjacent = _adjacent_codes(secret)

    with frozen_totp_time():
        with patch("src.core.database.valkey_client.get_client", return_value=None):
            first = verify_totp_code(
                secret,
                current,
                user_id=user_id,
                purpose="login",
                acting_user_id=user_id,
            )
            second = verify_totp_code(
                secret,
                adjacent,
                user_id=user_id,
                purpose="login",
                acting_user_id=user_id,
            )

    assert first is True, "the first valid code in the window must be accepted"
    assert second is False, (
        "a second, different code from the same validity window must be refused"
    )


def test_same_code_still_cannot_be_reused(monkeypatch):
    """The original per-code replay guard remains in force."""
    _isolated_cache(monkeypatch)
    secret = pyotp.random_base32()
    user_id = 987_654_322
    with frozen_totp_time():
        code = pyotp.TOTP(secret).now()
        with patch("src.core.database.valkey_client.get_client", return_value=None):
            assert (
                verify_totp_code(
                    secret,
                    code,
                    user_id=user_id,
                    purpose="login",
                    acting_user_id=user_id,
                )
                is True
            )
            assert (
                verify_totp_code(
                    secret,
                    code,
                    user_id=user_id,
                    purpose="login",
                    acting_user_id=user_id,
                )
                is False
            )


def test_purpose_scopes_remain_independent(monkeypatch):
    """A window claim is scoped to its purpose; other purposes still work."""
    _isolated_cache(monkeypatch)
    secret = pyotp.random_base32()
    user_id = 987_654_323
    with frozen_totp_time():
        code = pyotp.TOTP(secret).now()
        with patch("src.core.database.valkey_client.get_client", return_value=None):
            assert (
                verify_totp_code(
                    secret,
                    code,
                    user_id=user_id,
                    purpose="login",
                    acting_user_id=user_id,
                )
                is True
            )
            assert (
                verify_totp_code(
                    secret,
                    code,
                    user_id=user_id,
                    purpose="delete_account",
                    acting_user_id=user_id,
                )
                is True
            )
