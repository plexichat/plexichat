"""Regression tests for the bounded, fail-closed TOTP secret decryption cache.

The 2FA hot path re-reads and re-decrypts the same ciphertext on every check.
These tests pin the cache's correctness properties: it is keyed by the
non-secret user id, self-invalidates when the stored ciphertext changes, never
caches a failed decryption, stays bounded, and preserves the legacy uncached
path.
"""

import pytest

from src.core.auth import totp as totp_module


@pytest.fixture(autouse=True)
def _clean_secret_cache():
    totp_module.clear_totp_secret_cache()
    yield
    totp_module.clear_totp_secret_cache()


def test_repeated_decrypt_for_same_user_uses_cache(monkeypatch):
    """Repeated checks for one account decrypt only once."""
    calls = []

    def fake_decrypt(blob):
        calls.append(blob)
        return "JBSWY3DPEHPK3PXP"

    monkeypatch.setattr(totp_module, "decrypt_data", fake_decrypt)

    assert totp_module.decrypt_totp_secret("ENC:v1:blob", user_id=7) == (
        "JBSWY3DPEHPK3PXP"
    )
    for _ in range(5):
        assert totp_module.decrypt_totp_secret("ENC:v1:blob", user_id=7) == (
            "JBSWY3DPEHPK3PXP"
        )
    assert calls == ["ENC:v1:blob"]


def test_cache_is_keyed_by_non_secret_user_id(monkeypatch):
    """Different accounts never share a decrypted secret."""
    calls = []

    def fake_decrypt(blob):
        calls.append(blob)
        return "secret-for-" + str(len(calls))

    monkeypatch.setattr(totp_module, "decrypt_data", fake_decrypt)

    first = totp_module.decrypt_totp_secret("ENC:v1:blob", user_id=1)
    second = totp_module.decrypt_totp_secret("ENC:v1:blob", user_id=2)
    assert first != second
    assert len(calls) == 2
    # Repeating each id is a hit.
    assert totp_module.decrypt_totp_secret("ENC:v1:blob", user_id=1) == first
    assert totp_module.decrypt_totp_secret("ENC:v1:blob", user_id=2) == second
    assert len(calls) == 2


def test_rotated_ciphertext_is_not_served_from_stale_entry(monkeypatch):
    """A changed stored secret deterministically misses the old plaintext."""
    secrets = ["OLDSECRET", "NEWSECRET"]

    def fake_decrypt(blob):
        return secrets.pop(0)

    monkeypatch.setattr(totp_module, "decrypt_data", fake_decrypt)

    assert totp_module.decrypt_totp_secret("ENC:v1:old", user_id=3) == "OLDSECRET"
    # Same user, but the account's stored ciphertext was rotated/replaced.
    assert totp_module.decrypt_totp_secret("ENC:v1:new", user_id=3) == "NEWSECRET"
    assert secrets == []
    # The new value is now the warm entry.
    assert totp_module.decrypt_totp_secret("ENC:v1:new", user_id=3) == "NEWSECRET"


def test_decryption_failure_is_not_cached(monkeypatch):
    """Fail closed: an exception propagates and leaves no warm verdict."""
    calls = []

    def fake_decrypt(blob):
        calls.append(blob)
        if len(calls) == 1:
            raise ValueError("integrity check failed")
        return "GOODSECRET"

    monkeypatch.setattr(totp_module, "decrypt_data", fake_decrypt)

    with pytest.raises(ValueError):
        totp_module.decrypt_totp_secret("ENC:v1:blob", user_id=4)
    # A later successful decryption of the same blob must actually run, not
    # be served from a poisoned cache entry.
    assert totp_module.decrypt_totp_secret("ENC:v1:blob", user_id=4) == "GOODSECRET"
    assert len(calls) == 2


def test_legacy_uncached_path_always_decrypts(monkeypatch):
    """Omitting user_id preserves the original uncached behaviour."""
    calls = []

    def fake_decrypt(blob):
        calls.append(blob)
        return "SECRET"

    monkeypatch.setattr(totp_module, "decrypt_data", fake_decrypt)

    for _ in range(3):
        assert totp_module.decrypt_totp_secret("ENC:v1:blob") == "SECRET"
    assert len(calls) == 3


def test_cache_entry_count_is_bounded(monkeypatch):
    """The cache cannot grow past its configured entry ceiling."""
    monkeypatch.setattr(totp_module, "decrypt_data", lambda blob: "SECRET")
    cap = totp_module._TOTP_SECRET_CACHE.max_entries
    for uid in range(cap + 50):
        totp_module.decrypt_totp_secret(f"ENC:v1:blob-{uid}", user_id=uid)
    assert len(totp_module._TOTP_SECRET_CACHE) <= cap
