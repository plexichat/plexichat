"""Cross-cutting crypto regression suite.

The focused tests for each primitive live next to that primitive; this file is
the adversarial, end-to-end pass over the three failure classes the hardening
work set out to close:

* weak/abusive key-derivation parameters are refused and never leak to logs;
* malformed, broken or mismatched certificate chains fail closed;
* tampered HMAC tokens are rejected in constant time.

Each test is deterministic and fast (PBKDF2 uses the smallest policy-allowed
iteration count) so it belongs in the default unit run.
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

from src.core.security.kdf import (
    KDFParameterError,
    KDFParams,
    MIN_ITERATIONS,
    audit_kdf_params,
    derive_key,
    pbkdf2_raw,
)
from src.core.security.secrets import (
    generate_token_key,
    sign_token,
    verify_token,
)
from src.core.tls import _generate_self_signed_cert
from src.core.tls.chain_validation import TLSChainError, validate_certificate_bundle
from src.core.tls.ssl_settings import (
    MIN_TLS_VERSION,
    TLSHardeningError,
    build_ssl_context,
    validate_cipher_string,
    validate_min_tls_version,
)


class TestWeakDerivationParameters:
    @pytest.mark.parametrize(
        "params",
        [
            KDFParams(iterations=1000),
            KDFParams(iterations=0),
            KDFParams(iterations=-5),
            KDFParams(iterations=MIN_ITERATIONS - 1),
            KDFParams(salt_length=4),
            KDFParams(salt_length=0),
            KDFParams(salt_length=4096),
            KDFParams(dklen=8),
            KDFParams(dklen=2048),
            KDFParams(algorithm="md5"),
            KDFParams(algorithm="sha1"),
        ],
    )
    def test_weak_params_are_refused(self, params):
        with pytest.raises(KDFParameterError):
            validate_and_derive(params)

    def test_weak_salt_on_derive_is_refused(self):
        with pytest.raises(KDFParameterError):
            derive_key("pw", salt=b"short")

    def test_audit_never_contains_password_material(self):
        password = "Tr0ub4dor&3-DO-NOT-LOG"
        with patch("src.core.security.kdf.logger") as log:
            derived = derive_key(password)
        record = audit_kdf_params(derived.params)
        assert password not in str(record)
        assert password not in " ".join(str(call) for call in log.info.call_args_list)
        assert str(MIN_ITERATIONS) in str(record)

    def test_raw_primitive_refuses_zero_iterations(self):
        with pytest.raises(KDFParameterError):
            pbkdf2_raw(b"pw", b"salt" * 4, 0, 32)


def validate_and_derive(params):
    from src.core.security.kdf import validate_kdf_params

    validate_kdf_params(params)
    return derive_key("pw", params=params, do_audit=False)


class TestBadCertificateChains:
    @pytest.mark.parametrize(
        "payload",
        [
            b"",
            b"garbage",
            b"-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----",
            b"\x00\x01\x02\xff",
            b"a" * 1_100_000,
        ],
    )
    def test_parse_failures_fail_closed(self, payload):
        with pytest.raises(TLSChainError):
            validate_certificate_bundle(payload)

    def test_wrong_issuer_and_wrong_hostname(self, tmp_path):
        leaf, root, bundle = _make_chain(tmp_path)
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        other_root = _make_cert(
            "Other Root", "Other Root", other_key.public_key(), other_key, ca=True
        )
        with pytest.raises(TLSChainError, match="signature"):
            validate_certificate_bundle(_pem(leaf) + _pem(other_root))
        with pytest.raises(TLSChainError, match="hostname"):
            validate_certificate_bundle(bundle, hostname="attacker.example")

    def test_modified_pem_body_fails_closed(self, tmp_path):
        _, _, bundle = _make_chain(tmp_path)
        tampered = bytearray(bundle)
        # Flip a base64 character inside the first certificate body.
        start = tampered.index(b"-----BEGIN CERTIFICATE-----")
        target = start + len(b"-----BEGIN CERTIFICATE-----") + 10
        tampered[target] = ord("A") if tampered[target] != ord("A") else ord("B")
        with pytest.raises(TLSChainError):
            validate_certificate_bundle(bytes(tampered))

    def test_expired_and_ca_leaf_fail_closed(self, tmp_path):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.now(timezone.utc)
        expired = _make_cert(
            "expired",
            "expired",
            key.public_key(),
            key,
            ca=False,
            dns=["expired.example"],
            before=now - timedelta(days=10),
            after=now - timedelta(days=1),
        )
        with pytest.raises(TLSChainError, match="valid"):
            validate_certificate_bundle(_pem(expired), hostname="expired.example")
        ca_leaf = _make_cert("ca", "ca", key.public_key(), key, ca=True, dns=["ca.example"])
        with pytest.raises(TLSChainError, match="CA"):
            validate_certificate_bundle(_pem(ca_leaf))

    def test_wrong_pin_fails_closed(self, tmp_path):
        leaf, _, bundle = _make_chain(tmp_path)
        good_pin = hashlib.sha256(leaf.public_bytes(serialization.Encoding.DER)).hexdigest()
        validate_certificate_bundle(bundle, pins=[good_pin])
        with pytest.raises(TLSChainError, match="pin"):
            validate_certificate_bundle(bundle, pins=["b" * 64])


class TestTamperedTokens:
    def test_valid_token_roundtrip_and_tampering(self):
        with generate_token_key() as key:
            token = sign_token("session=42", key)
            assert verify_token("session=42", token, key) is True

            for tampered in (
                "session=43",
                "session=4",
                "session=42 ",
                "session=42\n",
            ):
                assert verify_token(tampered, token, key) is False, tampered

            last = "0" if token[-1] != "0" else "1"
            assert verify_token("session=42", token[:-1] + last, key) is False
            assert verify_token("session=42", token[:8], key) is False
            assert verify_token("session=42", token.upper(), key) is False
            assert verify_token("session=42", None, key) is False

    def test_token_from_another_key_is_rejected(self):
        token = sign_token("session=42", generate_token_key())
        with generate_token_key() as other_key:
            assert verify_token("session=42", token, other_key) is False

    def test_many_tampered_tokens_all_rejected(self):
        with generate_token_key() as key:
            good = sign_token("payload", key)
            for index in range(32):
                candidate = good[:index] + ("0" if good[index] != "0" else "1") + good[index + 1 :]
                assert verify_token("payload", candidate, key) is False


class TestWeakTLSSettings:
    @pytest.mark.parametrize("version", ["SSLv3", "TLSv1", "TLSv1.1"])
    def test_legacy_versions_refused(self, version):
        with pytest.raises(TLSHardeningError):
            validate_min_tls_version(version)

    @pytest.mark.parametrize("ciphers", ["RC4", "ALL", "3DES", "NULL"])
    def test_legacy_ciphers_refused(self, ciphers):
        with pytest.raises(TLSHardeningError):
            validate_cipher_string(ciphers)

    def test_context_floor_applies_to_generated_cert(self, tmp_path):
        cert = tmp_path / "server.crt"
        key = tmp_path / "server.key"
        _generate_self_signed_cert(str(cert), str(key), 30, "localhost")
        context = build_ssl_context(str(cert), str(key))
        assert context.minimum_version == MIN_TLS_VERSION


def _pem(cert) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def _make_cert(common_name, issuer_name, public_key, signing_key, *, ca, dns=(), before=None, after=None):
    now = datetime.now(timezone.utc)
    return (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_name)]))
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(before or now - timedelta(days=1))
        .not_valid_after(after or now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(name) for name in dns]),
            critical=False,
        )
        .sign(signing_key, hashes.SHA256())
    )


def _make_chain(tmp_path):
    root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    root = _make_cert("Root", "Root", root_key.public_key(), root_key, ca=True)
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = _make_cert(
        "leaf.example",
        "Root",
        leaf_key.public_key(),
        root_key,
        ca=False,
        dns=["leaf.example"],
    )
    return leaf, root, _pem(leaf) + _pem(root)
