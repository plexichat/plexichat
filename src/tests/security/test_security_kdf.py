"""Regression tests for bounded, audited key derivation.

Pins the known-answer vectors, the parameter bounds (weak and abusive values
must be refused), the audit record contents, constant-time verification and
the best-effort scrubbing of the transient secret buffer.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.core.security.kdf import (
    KDF_TEST_VECTORS,
    KDFParameterError,
    KDFParams,
    audit_kdf_params,
    derive_key,
    pbkdf2_raw,
    run_kdf_test_vectors,
    validate_kdf_params,
    verify_derived_key,
)


def test_known_answer_vectors_all_pass():
    assert run_kdf_test_vectors() is True
    assert KDF_TEST_VECTORS, "a vector set must remain pinned"


@pytest.mark.parametrize(
    "algorithm,password,salt,iterations,dklen,expected", KDF_TEST_VECTORS
)
def test_pbkdf2_vector(algorithm, password, salt, iterations, dklen, expected):
    assert pbkdf2_raw(password, salt, iterations, dklen, algorithm).hex() == expected


@pytest.mark.parametrize(
    "bad",
    [
        KDFParams(iterations=1_000),
        KDFParams(iterations=-1),
        KDFParams(iterations=50_000_000),
        KDFParams(salt_length=8),
        KDFParams(salt_length=1024),
        KDFParams(dklen=8),
        KDFParams(algorithm="md5"),
        {"iterations": 100_000},
    ],
)
def test_weak_or_malformed_params_rejected(bad):
    with pytest.raises(KDFParameterError):
        validate_kdf_params(bad)


def test_valid_params_are_accepted():
    params = KDFParams(iterations=210_000, salt_length=32, dklen=64)
    assert validate_kdf_params(params) == params


def test_derive_key_bounds_and_determinism():
    first = derive_key("correct horse battery staple")
    assert len(first.key) == 32
    assert len(first.salt) == 16
    second = derive_key("correct horse battery staple", salt=first.salt)
    assert second.key == first.key
    assert derive_key("another passphrase").salt != first.salt

    with pytest.raises(KDFParameterError):
        derive_key("pw", salt=b"too-short")
    with pytest.raises(KDFParameterError):
        derive_key("")
    with pytest.raises(KDFParameterError):
        derive_key("pw", params=KDFParams(iterations=10))


def test_audit_record_contains_parameters_not_secret():
    params = KDFParams(iterations=100_000, salt_length=16, dklen=32)
    record = audit_kdf_params(params)
    assert record["algorithm"] == "sha256"
    assert record["iterations"] == "100000"
    assert record["salt_length"] == "16"
    assert record["dklen"] == "32"
    assert not any("password" in key or "secret" in key for key in record)


def test_derive_key_logs_only_parameters():
    with patch("src.core.security.kdf.logger") as mock_logger:
        derive_key("a password that must never be logged")
    assert mock_logger.info.called
    message = mock_logger.info.call_args.args[0]
    assert "iterations=100000" in message
    assert "a password that must never be logged" not in message


def test_verify_derived_key_rejects_tampering():
    derived = derive_key("pw")
    assert verify_derived_key(
        "pw", derived.key, salt=derived.salt, params=derived.params
    )
    tampered = bytearray(derived.key)
    tampered[0] ^= 0x01
    assert not verify_derived_key(
        "pw", bytes(tampered), salt=derived.salt, params=derived.params
    )
    assert not verify_derived_key("pw", derived.key, salt=b"x" * 16)
    assert not verify_derived_key("pw", b"", salt=derived.salt)


def test_transient_secret_buffer_is_scrubbed():
    captured = {}

    def fake_raw(password, salt, iterations, dklen, algorithm):
        # Keep the actual mutable object the caller passed, not a copy, so the
        # post-call assertion observes the caller's scrubbing.
        captured["buffer"] = password
        return b"\x00" * dklen

    with patch("src.core.security.kdf.pbkdf2_raw", side_effect=fake_raw):
        derive_key("scrub-me")
    assert all(byte == 0 for byte in captured["buffer"])
