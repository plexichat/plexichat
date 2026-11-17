"""Regression tests for modern TLS settings in the server config path.

Covers the SSL context floor, forward-secret ciphers, the ban on legacy
renegotiation, and the config validation that makes an operator's weak override
fail closed instead of downgrading the server.
"""

from __future__ import annotations

import ssl
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.core.tls import (
    TLSConfigError,
    _generate_self_signed_cert,
    get_ssl_context,
    get_tls_config,
)
from src.core.tls.ssl_settings import (
    MIN_TLS_VERSION,
    SECURE_CIPHERS,
    TLSHardeningError,
    build_ssl_context,
    validate_cipher_string,
    validate_min_tls_version,
)


@pytest.fixture
def cert_pair(tmp_path):
    cert = tmp_path / "server.crt"
    key = tmp_path / "server.key"
    _generate_self_signed_cert(str(cert), str(key), 30, "localhost")
    return str(cert), str(key)


_WEAK_CIPHER_MARKERS = ("RC4", "3DES", "DES", "NULL", "MD5", "SHA1", "EXPORT", "PSK")


def test_context_enforces_tls12_floor_and_modern_ciphers(cert_pair):
    context = build_ssl_context(*cert_pair)
    assert isinstance(context, ssl.SSLContext)
    assert context.minimum_version == MIN_TLS_VERSION

    ciphers = context.get_ciphers()
    assert ciphers, "a usable cipher list must remain after hardening"
    for entry in ciphers:
        name = entry["name"].upper()
        assert not any(marker in name for marker in _WEAK_CIPHER_MARKERS), name
        # Every enabled suite is TLS 1.2 AEAD or a TLS 1.3 suite.
        assert "GCM" in name or "CHACHA20" in name or name.startswith("TLS_"), name


@pytest.mark.skipif(not getattr(ssl, "OP_NO_RENEGOTIATION", 0), reason="old OpenSSL")
def test_context_rejects_weak_renegotiation(cert_pair):
    context = build_ssl_context(*cert_pair)
    assert context.options & ssl.OP_NO_RENEGOTIATION
    assert context.options & ssl.OP_CIPHER_SERVER_PREFERENCE
    assert context.options & ssl.OP_NO_COMPRESSION


@pytest.mark.parametrize("bad", ["TLSv1", "SSLv3", "TLSv1.1", "TLS1_0", 1, object()])
def test_weak_min_tls_version_rejected(bad):
    with pytest.raises(TLSHardeningError):
        validate_min_tls_version(bad)


@pytest.mark.parametrize("good", [None, "TLSv1.2", "TLS1_3", ssl.TLSVersion.TLSv1_3])
def test_supported_min_tls_version_accepted(good):
    assert validate_min_tls_version(good).value >= MIN_TLS_VERSION.value


@pytest.mark.parametrize(
    "bad", ["RC4", "ALL", "DEFAULT:3DES", "aNULL", "MD5", "EXPORT", "PSK", "DES"]
)
def test_weak_cipher_string_rejected(bad):
    with pytest.raises(TLSHardeningError):
        validate_cipher_string(bad)


def test_valid_cipher_string_with_exclusions_accepted():
    assert (
        validate_cipher_string("ECDHE+AESGCM:!RC4:!3DES") == "ECDHE+AESGCM:!RC4:!3DES"
    )
    # The shipped list itself must validate under the same rules.
    assert validate_cipher_string(SECURE_CIPHERS) == SECURE_CIPHERS


def test_ssl_context_kwargs_use_hardened_factory(cert_pair):
    cert, key = cert_pair
    result = get_ssl_context(cert, key)
    assert result["ssl_certfile"] == cert
    assert result["ssl_keyfile"] == key
    assert result["ssl_ciphers"] == SECURE_CIPHERS
    factory = result["ssl_context_factory"]
    context = factory(SimpleNamespace(ssl_certfile=cert, ssl_keyfile=key), lambda: None)
    assert context.minimum_version == MIN_TLS_VERSION


def test_get_tls_config_rejects_weak_overrides(cert_pair):
    cert, key = cert_pair
    for override in ({"min_version": "TLSv1"}, {"ciphers": "RC4"}):
        cfg = {
            "enabled": True,
            "cert_path": cert,
            "key_path": key,
            **override,
        }
        with patch("src.core.tls.config") as mock_config:
            mock_config.get.return_value = cfg
            with pytest.raises(TLSConfigError):
                get_tls_config()


def test_get_tls_config_returns_hardened_settings(cert_pair):
    cert, key = cert_pair
    cfg = {"enabled": True, "cert_path": cert, "key_path": key}
    with patch("src.core.tls.config") as mock_config:
        mock_config.get.return_value = cfg
        result = get_tls_config()
    assert result["ssl_ciphers"] == SECURE_CIPHERS
    assert callable(result["ssl_context_factory"])
