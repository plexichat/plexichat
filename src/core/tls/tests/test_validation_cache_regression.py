"""Regression tests for the bounded TLS validation cache.

The cache must speed up repeated validation of identical certificate material
without ever restoring a verdict for rotated bytes or a different domain.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from src.core.tls import chain_validation as cv
from src.core.tls.validation_cache import BoundedTTLCache


@pytest.fixture(autouse=True)
def _clean_cache():
    """Isolate the module-level cache between tests."""
    cv.invalidate_validation_cache()
    yield
    cv.invalidate_validation_cache()


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> float:
        return self.t

    def now(self) -> datetime:
        return self.start + timedelta(seconds=self.t - 1000.0)


def _chain(hostname: str, *, days: int = 30) -> bytes:
    """Build a valid self-signed-root/leaf bundle for ``hostname``."""
    now = datetime.now(timezone.utc)
    root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Root")])
    root = (
        x509.CertificateBuilder()
        .subject_name(root_name)
        .issuer_name(root_name)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            True,
        )
        .sign(root_key, hashes.SHA256())
    )
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    leaf = (
        x509.CertificateBuilder()
        .subject_name(leaf_name)
        .issuer_name(root_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), False)
        .sign(root_key, hashes.SHA256())
    )
    return leaf.public_bytes(serialization.Encoding.PEM) + root.public_bytes(
        serialization.Encoding.PEM
    )


def test_cache_is_bounded_and_evicts_lru() -> None:
    clock = _Clock()
    cache: BoundedTTLCache[str, str] = BoundedTTLCache(
        max_entries=2, ttl_seconds=1000, clock=clock, now=clock.now
    )
    cache.put("a", "A")
    cache.put("b", "B")
    assert cache.get("a") == "A"  # a becomes most-recently used
    cache.put("c", "C")  # evicts b, not a

    assert len(cache) == 2
    assert cache.get("b") is None
    assert cache.get("a") == "A"
    assert cache.get("c") == "C"
    assert cache.stats.evictions == 1


def test_cache_ttl_expires_entries() -> None:
    clock = _Clock()
    cache: BoundedTTLCache[str, str] = BoundedTTLCache(
        max_entries=4, ttl_seconds=5, clock=clock, now=clock.now
    )
    cache.put("k", "V")
    clock.t += 4
    assert cache.get("k") == "V"
    clock.t += 1  # TTL reached
    assert cache.get("k") is None
    assert cache.stats.expirations == 1


def test_certificate_validity_bounds_cache_entry() -> None:
    clock = _Clock()
    cache: BoundedTTLCache[str, str] = BoundedTTLCache(
        max_entries=4, ttl_seconds=10_000, clock=clock, now=clock.now
    )
    expires = clock.now() + timedelta(seconds=3)
    cache.put("k", "V", valid_until=expires)
    clock.t += 2
    assert cache.get("k") == "V"  # TTL would still allow this
    clock.t += 1  # certificate validity reached
    assert cache.get("k") is None


def test_repeated_file_validation_hits_cache(tmp_path, monkeypatch) -> None:
    cert = tmp_path / "cert.pem"
    cert.write_bytes(_chain("a.example"))
    real_validate = cv.validate_certificate_bundle
    calls = []

    def counting(*args, **kwargs):
        calls.append(1)
        return real_validate(*args, **kwargs)

    monkeypatch.setattr(cv, "validate_certificate_bundle", counting)
    hits_before = cv.validation_cache_stats().hits
    first = cv.validate_certificate_file(str(cert), hostname="a.example")
    second = cv.validate_certificate_file(str(cert), hostname="a.example")

    assert len(calls) == 1
    assert second is first
    assert cv.validation_cache_stats().hits - hits_before == 1


def test_wrong_domain_is_not_resumed_from_cache(tmp_path) -> None:
    cert = tmp_path / "cert.pem"
    cert.write_bytes(_chain("a.example"))
    cv.validate_certificate_file(str(cert), hostname="a.example")
    hits_before = cv.validation_cache_stats().hits

    with pytest.raises(cv.TLSChainError):
        cv.validate_certificate_file(str(cert), hostname="b.example")

    # The failed wrong-domain lookup must not poison the good entry either.
    assert cv.validate_certificate_file(str(cert), hostname="a.example") is not None
    assert cv.validation_cache_stats().hits - hits_before == 1


def test_rotated_certificate_is_not_resumed_from_cache(tmp_path) -> None:
    cert = tmp_path / "cert.pem"
    cert.write_bytes(_chain("a.example"))
    cv.validate_certificate_file(str(cert), hostname="a.example")

    cert.write_bytes(_chain("b.example"))
    with pytest.raises(cv.TLSChainError):
        cv.validate_certificate_file(str(cert), hostname="a.example")

    assert cv.validate_certificate_file(str(cert), hostname="b.example") is not None


def test_failures_are_never_cached(tmp_path, monkeypatch) -> None:
    cert = tmp_path / "cert.pem"
    cert.write_bytes(b"not a certificate")
    real_validate = cv.validate_certificate_bundle
    calls = []

    def counting(*args, **kwargs):
        calls.append(1)
        return real_validate(*args, **kwargs)

    monkeypatch.setattr(cv, "validate_certificate_bundle", counting)
    hits_before = cv.validation_cache_stats().hits
    for _ in range(2):
        with pytest.raises(cv.TLSChainError):
            cv.validate_certificate_file(str(cert), hostname="a.example")
    assert len(calls) == 2
    assert cv.validation_cache_stats().hits - hits_before == 0


def test_explicit_invalidation_drops_entries(tmp_path) -> None:
    cert = tmp_path / "cert.pem"
    cert.write_bytes(_chain("a.example"))
    cv.validate_certificate_file(str(cert), hostname="a.example")
    assert cv.validation_cache_stats().size == 1

    cv.invalidate_validation_cache()
    assert cv.validation_cache_stats().size == 0
    hits_before = cv.validation_cache_stats().hits
    cv.validate_certificate_file(str(cert), hostname="a.example")
    assert cv.validation_cache_stats().hits - hits_before == 0
