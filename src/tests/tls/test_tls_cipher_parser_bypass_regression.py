"""Regression tests for TLS cipher-list parser bypasses.

OpenSSL accepts ``:``, ``,`` and whitespace as cipher-list separators, and it
also accepts IANA-style cipher names. A validator that split only on ``:`` or
that treated just ``-``/``+`` as MAC-segment boundaries could therefore be
tricked into accepting an ``@SECLEVEL`` directive or a CBC + SHA-1 suite:

* ``"ECDHE+AESGCM @SECLEVEL=0"`` passes a ':'-only split but OpenSSL parses the
  ``@SECLEVEL=0`` entry and lowers the context security level for the whole
  connection, re-enabling weak key sizes and SHA-1 signatures.
* ``"TLS_RSA_WITH_AES_128_CBC_SHA"`` passes a SHA-1 regex that only recognises
  ``-``/``+`` boundaries, but OpenSSL enables the weak ``AES128-SHA`` suite
  (CBC + SHA-1 MAC) for it.

Both are operator-controlled strings, so they must fail closed.
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
        "ECDHE+AESGCM @SECLEVEL=0",
        "ECDHE+AESGCM,@SECLEVEL=0",
        "ECDHE+AESGCM  @SECLEVEL=1",
        "ECDHE+AESGCM:!RC4 @SECLEVEL=0",
        "ECDHE+AESGCM @SECLEVEL=0:!RC4",
        # "@" in a token that would otherwise be skipped as an exclusion.
        "ECDHE+AESGCM:!@SECLEVEL=0",
    ],
)
def test_openssl_directive_with_alternate_separator_rejected(ciphers):
    with pytest.raises(TLSHardeningError, match="directives"):
        validate_cipher_string(ciphers)


@pytest.mark.parametrize(
    "ciphers",
    [
        "TLS_RSA_WITH_AES_128_CBC_SHA",
        "TLS_RSA_WITH_AES_256_CBC_SHA",
        "TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA",
        "ECDHE+AESGCM:TLS_RSA_WITH_AES_128_CBC_SHA",
        "ECDHE+AESGCM TLS_RSA_WITH_AES_128_CBC_SHA",
    ],
)
def test_iana_named_sha1_mac_ciphers_rejected(ciphers):
    with pytest.raises(TLSHardeningError, match="SHA-1 MAC"):
        validate_cipher_string(ciphers)


def test_hardened_defaults_and_clean_lists_still_accepted():
    assert validate_cipher_string(SECURE_CIPHERS) == SECURE_CIPHERS
    good = "ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-SHA384"
    assert validate_cipher_string(good) == good
    assert validate_cipher_string("ECDHE+AESGCM,!RC4 !3DES") == "ECDHE+AESGCM,!RC4 !3DES"
