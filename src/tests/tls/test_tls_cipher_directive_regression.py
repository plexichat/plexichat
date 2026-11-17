"""Regression tests: OpenSSL directives must not bypass the cipher floor.

``@SECLEVEL=0`` lowers OpenSSL's security level for the whole context,
re-enabling weak key sizes and SHA-1 signatures that the TLS 1.2 floor and the
AEAD cipher list are meant to exclude. A cipher string is operator input and
must not be able to override the hardening policy.
"""

import pytest

from src.core.tls.ssl_settings import (
    SECURE_CIPHERS,
    TLSHardeningError,
    validate_cipher_string,
)


@pytest.mark.parametrize(
    "ciphers",
    [
        "@SECLEVEL=0",
        "ECDHE+AESGCM:@SECLEVEL=0",
        "ECDHE+AESGCM:!RC4:@SECLEVEL=1",
        "@STRENGTH",
    ],
)
def test_openssl_directives_rejected(ciphers):
    with pytest.raises(TLSHardeningError):
        validate_cipher_string(ciphers)


def test_valid_cipher_lists_still_accepted():
    assert validate_cipher_string(SECURE_CIPHERS) == SECURE_CIPHERS
    assert (
        validate_cipher_string("ECDHE+AESGCM:!RC4:!3DES") == "ECDHE+AESGCM:!RC4:!3DES"
    )
