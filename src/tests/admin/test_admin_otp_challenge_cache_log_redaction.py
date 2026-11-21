"""Regression: admin OTP challenge tokens must not leak into caches or logs.

The challenge token minted after the admin password phase is a live
second-factor credential: until it expires, possession of it plus the OTP
code is enough to complete an admin login. It was being written verbatim into
the Valkey/process cache key and the per-challenge rate-limit key (readable by
anyone with ``KEYS`` access) and interpolated into a debug log line (copied to
shipped logs). Every store must hold only a one-way fingerprint while callers
keep presenting the raw token.
"""

import hashlib
from unittest.mock import MagicMock

import pytest

from src.core.admin.auth import helpers as ah
from src.core.admin.auth import otp_mixin as om

pytestmark = [pytest.mark.unit, pytest.mark.security]


@pytest.fixture
def fake_cache(monkeypatch):
    """Deterministic in-process stand-in for the shared cache backend."""
    store = {}
    written_keys = []
    deleted_keys = []

    def cache_set(key, value, ttl=None):
        written_keys.append(key)
        store[key] = value
        return True

    def cache_get(key):
        return store.get(key)

    def cache_delete(key):
        deleted_keys.append(key)
        store.pop(key, None)
        return True

    monkeypatch.setattr("src.core.database.cache_set", cache_set)
    monkeypatch.setattr("src.core.database.cache_get", cache_get)
    monkeypatch.setattr("src.core.database.cache_delete", cache_delete)
    return store, written_keys, deleted_keys


def test_challenge_token_is_hashed_in_cache_keys_and_still_round_trips(
    monkeypatch, fake_cache
):
    store, written_keys, deleted_keys = fake_cache
    token = "AdminOtpChallengeToken-0123456789abcdef"
    monkeypatch.setattr(ah.secrets, "token_urlsafe", lambda n: token)

    assert ah._create_otp_challenge(7, False) == token
    assert written_keys, "a cache write was expected"
    fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert any(fingerprint in key for key in written_keys)
    for key in set(written_keys) | set(store):
        assert token not in key, f"raw challenge token leaked into cache key {key!r}"

    # The one-way store must be transparent to the caller: presenting the raw
    # token again still validates and consumes the challenge.
    assert ah._validate_otp_challenge(token, 7, False) is True
    ah._consume_otp_challenge(token)
    assert deleted_keys and all(token not in key for key in deleted_keys)
    assert ah._validate_otp_challenge(token, 7, False) is False


def test_unknown_challenge_debug_log_does_not_embed_raw_token(monkeypatch, fake_cache):
    fake_cache  # ensure the cache backend is patched to an empty store
    token = "UnknownChallengeToken-fedcba9876543210"
    logged = []

    class _Logger:
        def debug(self, msg, *args, **kwargs):
            logged.append(str(msg))

        def warning(self, msg, *args, **kwargs):
            pass

    monkeypatch.setattr(ah, "logger", _Logger())

    assert ah._validate_otp_challenge(token, 7, False) is False
    assert logged, "an unknown challenge must still emit a diagnostic"
    assert token not in "\n".join(logged), logged


def test_verify_otp_rate_limit_key_does_not_embed_challenge_token(monkeypatch):
    token = "ChallengeTokenThroughVerifyOTP-abc123XYZ"
    seen_keys = []

    def check_rate_limit(key, limit, window):
        seen_keys.append(key)
        return True, limit

    monkeypatch.setattr("src.core.database.check_rate_limit", check_rate_limit)
    monkeypatch.setattr(om, "_validate_otp_challenge", lambda *a, **k: True)
    monkeypatch.setattr(om, "_consume_otp_challenge", lambda *a, **k: None)
    monkeypatch.setattr(om, "_check_admin_otp_budget", lambda admin_id: True)
    fake_totp = MagicMock()
    fake_totp.verify.return_value = False
    monkeypatch.setattr(om.pyotp, "TOTP", lambda secret: fake_totp)

    db = MagicMock()

    def fetch_one(sql, params=None):
        if "otp_last_used_code" in sql:
            return {"otp_last_used_code": None, "otp_last_used_at": None}
        return {
            "totp_secret": "SECRET",
            "totp_secret_encrypted": None,
            "backup_codes": None,
            "backup_codes_hash": None,
        }

    db.fetch_one.side_effect = fetch_one
    mixin = object.__new__(om.OTPMixin)
    mixin._db = db

    result = mixin.verify_otp(7, "000000", token)
    assert result.success is False
    assert seen_keys, "an OTP attempt must consume a rate-limit bucket"
    assert not any(token in key for key in seen_keys), seen_keys
