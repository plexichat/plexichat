"""License challenge/nonce work stays bounded without weakening validation."""

import base64

import src.api.routes.public_license as pl
from src.utils.common_utils.utils.licensing.core import LicenseManager


def test_issue_does_not_sweep_on_every_call(monkeypatch):
    store = pl.NonceStore(ttl_seconds=300)
    monkeypatch.setattr(store, "_get_valkey", lambda: None)

    sweeps = []
    real_sweep = store._purge_expired_locked

    def counting(now=None):
        sweeps.append(1)
        return real_sweep(now)

    monkeypatch.setattr(store, "_purge_expired_locked", counting)

    for _ in range(10):
        nonce, ttl = store.issue()
        assert nonce and ttl == 300

    # Below the threshold (and inside the interval) no full scan is performed.
    assert sweeps == []


def test_expired_nonce_is_still_rejected_without_a_sweep(monkeypatch):
    """Deferring the sweep must not let an expired nonce be consumed."""
    store = pl.NonceStore(ttl_seconds=300)
    monkeypatch.setattr(store, "_get_valkey", lambda: None)

    nonce, _ = store.issue()
    with store._lock:
        store._nonces[nonce] = 0.0  # force expiry without a sweep

    assert store.consume(nonce) is False


def test_public_key_is_decoded_once(monkeypatch):
    key = base64.b64encode(b"x" * 32).decode("ascii")
    monkeypatch.setattr(LicenseManager, "_PUBLIC_KEY_BASE64", key, raising=False)
    pl._public_key_cache.clear()

    real_decode = base64.b64decode
    calls = []

    def counting(*args, **kwargs):
        calls.append(1)
        return real_decode(*args, **kwargs)

    monkeypatch.setattr(pl.base64, "b64decode", counting)

    assert pl._get_public_key_bytes() == b"x" * 32
    assert pl._get_public_key_bytes() == b"x" * 32
    assert len(calls) == 1


def test_hot_swapped_key_is_decoded_fresh(monkeypatch):
    first = base64.b64encode(b"a" * 32).decode("ascii")
    second = base64.b64encode(b"b" * 32).decode("ascii")
    monkeypatch.setattr(LicenseManager, "_PUBLIC_KEY_BASE64", first, raising=False)
    pl._public_key_cache.clear()

    assert pl._get_public_key_bytes() == b"a" * 32
    monkeypatch.setattr(LicenseManager, "_PUBLIC_KEY_BASE64", second, raising=False)
    assert pl._get_public_key_bytes() == b"b" * 32
