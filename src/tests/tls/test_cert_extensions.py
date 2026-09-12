"""TLS self-signed certificate extension regressions (Agent 33 F2).

The audit flagged a possible ``BasicConstraints(ca=True)`` on the
generated self-signed certificate, which would make the server's leaf
certificate a CA capable of signing arbitrary child certificates and
break the chain-of-trust expectation. Current code sets ``ca=False``;
this test pins the full extension contract so the property cannot
regress.
"""

from __future__ import annotations

from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID

from src.core.tls import _generate_self_signed_cert


def _generate(tmp_path) -> x509.Certificate:
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    _generate_self_signed_cert(
        str(cert_path), str(key_path), validity_days=30, hostname="localhost"
    )
    assert cert_path.is_file(), "certificate must be written"
    assert key_path.is_file(), "private key must be written"
    return x509.load_pem_x509_certificate(cert_path.read_bytes())


def test_leaf_certificate_is_not_a_ca(tmp_path):
    cert = _generate(tmp_path)

    basic = cert.extensions.get_extension_for_class(x509.BasicConstraints)
    assert basic.critical is True, "BasicConstraints must be marked critical"
    assert basic.value.ca is False, (
        "self-signed leaf must have ca=False; ca=True would let the server "
        "certificate sign arbitrary child certificates"
    )
    assert basic.value.path_length is None

    usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    assert usage.key_cert_sign is False, (
        "key_cert_sign must be False so the key cannot sign child certificates"
    )
    assert usage.crl_sign is False, "crl_sign must be False for a leaf"


def test_leaf_certificate_is_server_auth_only(tmp_path):
    cert = _generate(tmp_path)

    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert list(eku) == [ExtendedKeyUsageOID.SERVER_AUTH], (
        "self-signed server cert must be restricted to serverAuth"
    )
    assert ExtendedKeyUsageOID.CLIENT_AUTH not in eku


def test_leaf_certificate_has_san_and_validity(tmp_path):
    cert = _generate(tmp_path)

    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    dns_names = san.get_values_for_type(x509.DNSName)
    assert "localhost" in dns_names, "hostname must appear in the SAN list"

    # Not valid after must be in the future; not before <= not after.
    assert cert.not_valid_after_utc > cert.not_valid_before_utc
    assert cert.serial_number > 0
