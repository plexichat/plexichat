"""Deep hardening regressions for config paths, TLS ciphers and chains.

Three real weaknesses are pinned here:

* operator-supplied TLS cert/key paths with ``..`` traversal or embedded
  control characters used to reach file reads/writes after abspath collapsed
  them (the hardened ``path_validation`` helpers were dead code in the startup
  path);
* an enabled cipher whose MAC is bare ``SHA`` (OpenSSL's SHA-1 alias), e.g.
  ``AES128-SHA``, slipped past the literal-``SHA1`` substring check;
* ``verify_chain`` accepted a non-CA intermediate that had signed the leaf,
  because it only checked signatures and the leaf's constraints.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from src.core.tls import (
    TLSConfigError,
    _generate_self_signed_cert,
    ensure_certificates,
    get_tls_config,
)
from src.core.tls.chain_validation import TLSChainError, verify_chain
from src.core.tls.path_validation import TLSPathError, validate_config_path_tokens
from src.core.tls.ssl_settings import (
    SECURE_CIPHERS,
    TLSHardeningError,
    validate_cipher_string,
)


# ── 1. config path traversal / control characters ───────────────────────────


class TestConfigPathTokens:
    def test_rejects_dotdot_traversal(self):
        with pytest.raises(TLSPathError, match="traversal"):
            validate_config_path_tokens("/etc/tls/../../etc/shadow.crt")

    def test_rejects_windows_dotdot_traversal(self):
        with pytest.raises(TLSPathError, match="traversal"):
            validate_config_path_tokens("C:\\tls\\..\\..\\secret.key")

    @pytest.mark.parametrize(
        "bad", ["/tmp/a\x00b.crt", "/tmp/a\rb.crt", "/tmp/a\nb.crt"]
    )
    def test_rejects_control_characters(self, bad):
        with pytest.raises(TLSPathError):
            validate_config_path_tokens(bad)

    def test_accepts_clean_absolute_path(self):
        assert (
            validate_config_path_tokens("/etc/tls/server.crt") == "/etc/tls/server.crt"
        )

    def test_get_tls_config_rejects_traversal_before_read(self, tmp_path):
        cert = tmp_path / "server.crt"
        key = tmp_path / "server.key"
        _generate_self_signed_cert(str(cert), str(key), 30, "localhost")
        cfg = {
            "enabled": True,
            # Collapses to a real cert, but the traversal must be refused.
            "cert_path": str(tmp_path / "sub" / ".." / "server.crt"),
            "key_path": str(key),
        }
        with patch("src.core.tls.config") as mock_config:
            mock_config.get.return_value = cfg
            with pytest.raises(TLSConfigError, match="traversal"):
                get_tls_config()

    def test_ensure_certificates_rejects_config_traversal(self, tmp_path):
        cfg = {
            "cert_path": str(tmp_path / "sub" / ".." / "x.crt"),
            "key_path": str(tmp_path / "x.key"),
        }
        with patch("src.core.tls.config") as mock_config:
            mock_config.get.return_value = cfg
            with pytest.raises(TLSConfigError, match="traversal"):
                ensure_certificates()


# ── 2. weak TLS default: SHA-1 MAC ciphers ──────────────────────────────────


class TestCipherHardening:
    @pytest.mark.parametrize(
        "bad",
        [
            "AES128-SHA",
            "AES256-SHA",
            "ECDHE-RSA-AES128-SHA",
            "DHE-RSA-AES256-SHA",
        ],
    )
    def test_sha1_mac_ciphers_rejected(self, bad):
        with pytest.raises(TLSHardeningError, match="SHA-1 MAC"):
            validate_cipher_string(bad)

    def test_sha2_aead_ciphers_still_accepted(self):
        good = "ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-SHA384"
        assert validate_cipher_string(good) == good

    def test_shipped_list_and_exclusions_still_accepted(self):
        assert validate_cipher_string(SECURE_CIPHERS) == SECURE_CIPHERS
        assert validate_cipher_string("ECDHE+AESGCM:!RC4:!3DES") == (
            "ECDHE+AESGCM:!RC4:!3DES"
        )


# ── 3. certificate validation gap: non-CA issuers ───────────────────────────


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _cert(subject, issuer, public_key, signing_key, *, ca):
    now = datetime.now(timezone.utc)
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .sign(signing_key, hashes.SHA256())
    )


class TestChainIssuerAuthority:
    def test_non_ca_intermediate_rejected(self):
        root_key = _key()
        root = _cert(
            _name("Root"), _name("Root"), root_key.public_key(), root_key, ca=True
        )
        inter_key = _key()
        inter = _cert(
            _name("Inter"),
            root.subject,
            inter_key.public_key(),
            root_key,
            ca=False,
        )
        leaf_key = _key()
        leaf = _cert(
            _name("Leaf"),
            inter.subject,
            leaf_key.public_key(),
            inter_key,
            ca=False,
        )
        with pytest.raises(TLSChainError, match="not a CA"):
            verify_chain([leaf, inter, root])

    def test_ca_intermediate_without_keycertsign_rejected(self):
        root_key = _key()
        root = _cert(
            _name("Root"), _name("Root"), root_key.public_key(), root_key, ca=True
        )
        inter_key = _key()
        now = datetime.now(timezone.utc)
        inter = (
            x509.CertificateBuilder()
            .subject_name(_name("Inter"))
            .issuer_name(root.subject)
            .public_key(inter_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=30))
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=None), critical=True
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=True,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(root_key, hashes.SHA256())
        )
        leaf_key = _key()
        leaf = _cert(
            _name("Leaf"),
            inter.subject,
            leaf_key.public_key(),
            inter_key,
            ca=False,
        )
        with pytest.raises(TLSChainError, match="may not sign"):
            verify_chain([leaf, inter, root])
