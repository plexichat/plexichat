"""Regression: weak OpenSSL cipher aliases must be rejected by validation.

``validate_cipher_string`` blocks the literal ``EXPORT`` token but missed the
equivalent short alias ``EXP`` and the deprecated GOST cipher family. Those
aliases expand to export-grade / weak suites on builds that still honour them,
so a cipher string like ``ECDHE+AESGCM:EXP`` passed validation while silently
enabling weak ciphers alongside the strong ones. Every weak alias must fail
closed, including when mixed into an otherwise-strong list.
"""

from __future__ import annotations

import pytest

from src.core.tls.ssl_settings import (
    SECURE_CIPHERS,
    TLSHardeningError,
    validate_cipher_string,
)


@pytest.mark.parametrize(
    "ciphers",
    [
        "EXP",
        "EXPORT",
        "ECDHE+AESGCM:EXP",
        "ECDHE+AESGCM,EXP",
        "ECDHE+AESGCM EXP",
        "!RC4:EXP",
    ],
)
def test_export_cipher_aliases_rejected(ciphers):
    with pytest.raises(TLSHardeningError, match="deprecated cipher"):
        validate_cipher_string(ciphers)


@pytest.mark.parametrize(
    "ciphers",
    [
        "GOST",
        "aGOST",
        "GOST89MAC",
        "GOST12",
        "ECDHE+AESGCM:GOST",
        "ECDHE+AESGCM,aGOST01",
    ],
)
def test_gost_cipher_aliases_rejected(ciphers):
    with pytest.raises(TLSHardeningError, match="deprecated cipher"):
        validate_cipher_string(ciphers)


@pytest.mark.parametrize(
    "ciphers",
    [
        SECURE_CIPHERS,
        "ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-SHA384",
        "ECDHE+AESGCM,!RC4 !3DES",
    ],
)
def test_strong_cipher_lists_still_accepted(ciphers):
    assert validate_cipher_string(ciphers) == ciphers
