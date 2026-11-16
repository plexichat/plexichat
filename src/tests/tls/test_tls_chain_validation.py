"""Regression tests for certificate chain / hostname validation.

A PEM that parses is not necessarily a usable identity. These tests pin the
fail-closed behaviour for malformed material, broken chains, CA leaves,
expired certificates, wrong hostnames and mismatched pins.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from src.core.tls import TLSConfigError, _generate_self_signed_cert, get_tls_config
from src.core.tls.chain_validation import (
    TLSChainError,
    validate_certificate_bundle,
    validate_certificate_file,
    verify_chain,
    verify_hostname,
    verify_pin,
)


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _cert(
    subject,
    issuer,
    public_key,
    signing_key,
    *,
    ca: bool,
    dns_names=(),
    not_before=None,
    not_after=None,
) -> x509.Certificate:
    now = datetime.now(timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before or now - timedelta(days=1))
        .not_valid_after(not_after or now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=ca,
                crl_sign=ca,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    )
    if dns_names:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(n) for n in dns_names]),
            critical=False,
        )
    return builder.sign(signing_key, hashes.SHA256())


def _pem(cert) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


@pytest.fixture
def chain():
    root_key = _key()
    root = _cert(_name("Test Root"), _name("Test Root"), root_key.public_key(), root_key, ca=True)
    leaf_key = _key()
    leaf = _cert(
        _name("leaf.example.com"),
        root.subject,
        leaf_key.public_key(),
        root_key,
        ca=False,
        dns_names=["leaf.example.com", "*.wild.example.com"],
    )
    return leaf, root, _pem(leaf) + _pem(root)


def test_valid_chain_and_hostname(chain):
    leaf, root, bundle = chain
    result = validate_certificate_bundle(bundle, hostname="leaf.example.com")
    assert result.leaf.subject == leaf.subject
    assert len(result.certificates) == 2


@pytest.mark.parametrize(
    "bad", [b"", b"not a certificate", b"-----BEGIN CERTIFICATE-----\ntrash\n-----END CERTIFICATE-----", b"\xff\xfe\x00", b"a" * 2_000_000]
)
def test_parse_errors_fail_closed(bad):
    with pytest.raises(TLSChainError):
        validate_certificate_bundle(bad)


def test_broken_chain_signature(chain):
    leaf, root, _ = chain
    other_key = _key()
    other_root = _cert(
        _name("Other Root"),
        _name("Other Root"),
        other_key.public_key(),
        other_key,
        ca=True,
    )
    with pytest.raises(TLSChainError):
        validate_certificate_bundle(_pem(leaf) + _pem(other_root))


def test_certificate_outside_validity(chain):
    leaf, root, _ = chain
    expired = _cert(
        leaf.subject,
        root.subject,
        _key().public_key(),
        _key(),
        ca=False,
        dns_names=["expired.example.com"],
        not_before=datetime.now(timezone.utc) - timedelta(days=30),
        not_after=datetime.now(timezone.utc) - timedelta(days=1),
    )
    bad_bundle = _pem(expired) + _pem(root)
    with pytest.raises(TLSChainError):
        validate_certificate_bundle(bad_bundle, hostname="expired.example.com")


def test_ca_leaf_rejected():
    key = _key()
    ca_leaf = _cert(_name("evil"), _name("evil"), key.public_key(), key, ca=True)
    with pytest.raises(TLSChainError, match="must not be a CA"):
        validate_certificate_bundle(_pem(ca_leaf))


def test_hostname_must_match_san(chain):
    leaf, root, bundle = chain
    with pytest.raises(TLSChainError):
        validate_certificate_bundle(bundle, hostname="evil.example.com")
    # Wildcards cover exactly one label, not deeper subdomains.
    verify_hostname(leaf, "api.wild.example.com")
    with pytest.raises(TLSChainError):
        verify_hostname(leaf, "deep.api.wild.example.com")


def test_no_cn_fallback(chain):
    leaf, _, _ = chain
    no_san = _cert(leaf.subject, leaf.subject, _key().public_key(), _key(), ca=False)
    with pytest.raises(TLSChainError, match="SubjectAlternativeName"):
        verify_hostname(no_san, "leaf.example.com")


def test_pin_matches_and_mismatch_fails(chain):
    leaf, _, bundle = chain
    pin = hashlib.sha256(leaf.public_bytes(serialization.Encoding.DER)).hexdigest()
    validate_certificate_bundle(bundle, pins=[pin])
    with pytest.raises(TLSChainError, match="pin"):
        validate_certificate_bundle(bundle, pins=["a" * 64])
    with pytest.raises(TLSChainError):
        verify_pin(leaf, "not-a-fingerprint")


def test_generated_self_signed_cert_validates(tmp_path):
    cert = tmp_path / "server.crt"
    key = tmp_path / "server.key"
    _generate_self_signed_cert(str(cert), str(key), 30, "chat.example.com")
    result = validate_certificate_file(str(cert), hostname="chat.example.com")
    san = result.leaf.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value
    assert "chat.example.com" in [entry.value for entry in san]


def test_get_tls_config_fails_closed_on_malformed_cert(tmp_path):
    cert = tmp_path / "bad.crt"
    key = tmp_path / "bad.key"
    cert.write_bytes(b"not a certificate")
    key.write_text("irrelevant")
    cfg = {
        "enabled": True,
        "cert_path": str(cert),
        "key_path": str(key),
        "hostname": "chat.example.com",
    }
    with patch("src.core.tls.config") as mock_config:
        mock_config.get.return_value = cfg
        with pytest.raises(TLSConfigError):
            get_tls_config()


def test_verify_chain_requires_trust_anchor(chain):
    leaf, _, _ = chain
    with pytest.raises(TLSChainError):
        verify_chain([leaf])
