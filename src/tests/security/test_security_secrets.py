"""Regression tests for constant-time comparison and secret scrubbing.

Pins the canonical comparison helper (including non-ASCII handling), the
mutable scrub-on-exit buffer, HMAC token verification and the wiring of these
primitives into the KDF and TLS pin paths.
"""

from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from src.core.security import (
    SecretBuffer,
    constant_time_compare,
    derive_key,
    generate_token_key,
    scrub_secret,
    sign_token,
    verify_derived_key,
    verify_token,
)
from src.core.tls import _generate_self_signed_cert
from src.core.tls.chain_validation import verify_pin


def test_constant_time_compare_values():
    assert constant_time_compare("secret", "secret") is True
    assert constant_time_compare("secret", "secreT") is False
    assert constant_time_compare(b"\x00\xff", b"\x00\xff") is True
    assert constant_time_compare("pässwörd", "pässwörd") is True
    assert constant_time_compare("pässwörd", "pässwörd!") is False
    assert constant_time_compare("pässwörd", b"p\xc3\xa4ssw\xc3\xb6rd") is True
    assert constant_time_compare("", "") is True


@pytest.mark.parametrize("bad", [None, 123, object(), ["a"]])
def test_constant_time_compare_rejects_non_secret_types(bad):
    with pytest.raises(TypeError):
        constant_time_compare(bad, "x")


def test_scrub_secret_only_scrubs_mutable_buffers():
    mutable = bytearray(b"s3cr3t")
    assert scrub_secret(mutable) is True
    assert bytes(mutable) == b"\x00" * len(mutable)

    view = memoryview(bytearray(b"s3cr3t"))
    assert scrub_secret(view) is True
    assert bytes(view) == b"\x00" * len(view)

    assert scrub_secret(b"s3cr3t") is False
    assert scrub_secret("s3cr3t") is False
    assert scrub_secret(memoryview(b"readonly")) is False


def test_secret_buffer_scrubs_on_exit():
    with SecretBuffer("s3cr3t") as secret:
        assert secret.bytes == b"s3cr3t"
        assert len(secret) == 6
        captured = secret.view
    assert all(byte == 0 for byte in captured)
    with pytest.raises(ValueError):
        _ = secret.bytes


def test_secret_buffer_rejects_non_secret_data():
    with pytest.raises(TypeError):
        SecretBuffer(123)


def test_hmac_token_verification():
    with generate_token_key() as key:
        token = sign_token("user:42", key)
        assert verify_token("user:42", token, key) is True
        assert verify_token("user:43", token, key) is False
        assert (
            verify_token(
                "user:42", token[:-1] + ("0" if token[-1] != "0" else "1"), key
            )
            is False
        )
        assert verify_token("user:42", None, key) is False
        assert verify_token("user:42", token.upper(), key) is False
        assert verify_token("größe", sign_token("größe", key), key) is True
        with pytest.raises(ValueError):
            sign_token("x", b"short-key")


def test_kdf_uses_canonical_constant_time_comparison():
    derived = derive_key("pw", salt=b"0" * 16)
    with patch(
        "src.core.security.kdf.constant_time_compare", wraps=constant_time_compare
    ) as spy:
        assert verify_derived_key(
            "pw", derived.key, salt=derived.salt, params=derived.params
        )
    assert spy.called


def test_tls_pin_check_uses_canonical_constant_time_comparison(tmp_path):
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    _generate_self_signed_cert(str(cert_path), str(key_path), 30, "localhost")
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    digest = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    with patch(
        "src.core.tls.chain_validation.constant_time_compare",
        wraps=constant_time_compare,
    ) as spy:
        verify_pin(cert, digest)
    assert spy.called
