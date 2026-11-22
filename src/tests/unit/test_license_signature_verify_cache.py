"""Signature verification is a content invariant: verify once, not per poll.

``validate_license_payload`` builds a fresh ``LicenseManager`` on every admin
``/license/validate`` request and re-runs the Ed25519 verification of the same
submitted license document. The verdict ("does this signature match this
canonical license payload under this public key?") depends only on the license
*content*, the signature and the public key -- never on wall-clock time and
never on the caller. It is therefore safe to memoize keyed on that content, and
a changed document simply misses the cache (no verdict survives a license
change). These tests count the actual crypto verification calls.
"""

import base64
import json

import pytest

from utils.licensing import core as core
import utils.licensing as lic


def _configure_key(monkeypatch, public):
    """Point ``validate_license_payload`` at this test's verification key."""
    monkeypatch.setattr(
        lic, "_PLEXICHAT_PUBLIC_KEY_BASE64", base64.b64encode(public).decode()
    )


def _signed_payload(private, instance_id="cache-test", features=None):
    payload = {
        "version": "1.0",
        "type": "standard",
        "instance_id": instance_id,
        "issued_at": 1_700_000_000,
        "expires_at": 2_100_000_000,
        "features": features if features is not None else {"all": True},
        "limits": {"max_users": 50},
    }
    payload["signature"] = core.sign_license(private, payload)
    return payload


@pytest.fixture
def counting_ed25519(monkeypatch):
    """Count real ``Ed25519PublicKey.from_public_bytes`` constructions."""
    import cryptography.hazmat.primitives.asymmetric.ed25519 as ed

    real_cls = ed.Ed25519PublicKey
    calls = []

    class WrappedKey:
        def __init__(self, real):
            self._real = real

        def verify(self, signature, data):
            calls.append(1)
            return self._real.verify(signature, data)

    class CountingKey:
        @classmethod
        def from_public_bytes(cls, data):
            return WrappedKey(real_cls.from_public_bytes(data))

    monkeypatch.setattr(ed, "Ed25519PublicKey", CountingKey)
    clear = getattr(core, "clear_signature_verification_cache", None)
    if clear is not None:
        clear()
    return calls


def test_repeated_validation_verifies_signature_once(counting_ed25519, monkeypatch):
    """The hot path: N polls of the same license must verify once, not N times."""
    private, public = core.generate_keypair()
    _configure_key(monkeypatch, public)
    raw = json.dumps(_signed_payload(private)).encode()

    results = [lic.validate_license_payload(raw) for _ in range(8)]

    assert all(result["valid"] is True for result in results)
    assert len(counting_ed25519) == 1, (
        "signature re-verified on every validate call despite identical content"
    )


def test_changed_license_content_reverifies(counting_ed25519, monkeypatch):
    """A different license document must miss the cache and verify afresh."""
    private, public = core.generate_keypair()
    _configure_key(monkeypatch, public)
    raw_a = json.dumps(_signed_payload(private, instance_id="instance-a")).encode()
    raw_b = json.dumps(_signed_payload(private, instance_id="instance-b")).encode()

    assert lic.validate_license_payload(raw_a)["valid"] is True
    assert lic.validate_license_payload(raw_a)["valid"] is True
    assert len(counting_ed25519) == 1

    assert lic.validate_license_payload(raw_b)["valid"] is True
    assert len(counting_ed25519) == 2, "changed content reused the old verdict"


def test_invalid_signature_stays_fail_closed_and_is_not_cached_as_valid(
    counting_ed25519, monkeypatch
):
    """A forged document never verifies, and is never served from a valid memo."""
    private, public = core.generate_keypair()
    _configure_key(monkeypatch, public)
    payload = _signed_payload(private)
    payload["features"] = {"all": True, "sso": True}  # tamper after signing
    raw = json.dumps(payload).encode()

    for _ in range(3):
        assert lic.validate_license_payload(raw)["valid"] is False


def test_cache_is_bounded(counting_ed25519, monkeypatch):
    """Distinct documents cannot grow the verification cache without limit."""
    private, public = core.generate_keypair()
    _configure_key(monkeypatch, public)
    for i in range(500):
        lic.validate_license_payload(
            json.dumps(_signed_payload(private, instance_id=f"id-{i}")).encode()
        )
    cache = getattr(core, "_verify_result_cache", {})
    cap = getattr(core, "_MAX_VERIFY_CACHE_ENTRIES", 0)
    assert len(cache) <= cap
