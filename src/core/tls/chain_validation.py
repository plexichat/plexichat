"""Certificate chain and hostname validation for configured TLS material.

A certificate that merely parses is not proof that the server is presenting a
usable identity: the chain may be broken, the leaf may be a CA, the certificate
may be expired, or the SAN may not cover the configured hostname. This module
checks all of those and, importantly, treats *any* parse failure as a refusal
rather than a "could not check, carry on" — a malformed PEM must never put a
server into HTTPS with an unverified identity.

The chain model is deliberately simple and closed: certificates are supplied
as one PEM bundle in leaf-to-root order; every signature is checked, the last
certificate must be a self-signed trust anchor, and hostname matching uses SAN
entries only (no Common Name fallback).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence

from src.core.security.hardening import bounded_int

from .config_validation import TLSConfigValidationError, validate_fingerprint, validate_hostname

MAX_CHAIN_CERTS = 10
MAX_CERT_BYTES = 1_000_000
_PEM_BLOCK = re.compile(
    rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.DOTALL
)


class TLSChainError(ValueError):
    """A certificate chain, hostname or pin failed validation."""


@dataclass(frozen=True)
class ValidatedChain:
    """The parsed chain after every check passed."""

    leaf: Any
    certificates: tuple[Any, ...]
    hostname: Optional[str]


def _x509():
    try:
        from cryptography import x509
    except ImportError as exc:  # pragma: no cover - cryptography is a dependency
        raise TLSChainError("cryptography is required for certificate validation") from exc
    return x509


def _as_utc(value: Any) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def load_certificates(
    pem_bytes: bytes,
    *,
    max_bytes: int = MAX_CERT_BYTES,
    max_certs: int = MAX_CHAIN_CERTS,
) -> list[Any]:
    """Parse a PEM bundle into certificates, failing closed on any parse error."""
    x509 = _x509()
    if not isinstance(pem_bytes, (bytes, bytearray)) or not pem_bytes:
        raise TLSChainError("certificate bundle is empty")
    bounded = bounded_int(max_bytes, "max_bytes", minimum=1, maximum=10 * 1024 * 1024)
    bounded_int(max_certs, "max_certs", minimum=1, maximum=100)
    if len(pem_bytes) > bounded:
        raise TLSChainError("certificate bundle is too large")

    blocks = _PEM_BLOCK.findall(bytes(pem_bytes))
    if not blocks:
        raise TLSChainError("certificate bundle has no PEM certificate block")
    if len(blocks) > max_certs:
        raise TLSChainError("certificate chain is too long")

    certificates: list[Any] = []
    for block in blocks:
        try:
            certificates.append(x509.load_pem_x509_certificate(block))
        except Exception as exc:
            # cryptography raises a family of ValueError/UnsupportedAlgorithm
            # types depending on the malformed field. All of them are fatal.
            raise TLSChainError("certificate could not be parsed") from exc
    return certificates


def _check_validity(cert: Any, now: datetime) -> None:
    try:
        if hasattr(cert, "not_valid_before_utc"):
            not_before = _as_utc(cert.not_valid_before_utc)
        else:
            not_before = _as_utc(cert.not_valid_before)
        if hasattr(cert, "not_valid_after_utc"):
            not_after = _as_utc(cert.not_valid_after_utc)
        else:
            not_after = _as_utc(cert.not_valid_after)
    except (AttributeError, TypeError, ValueError) as exc:
        raise TLSChainError("certificate validity dates could not be read") from exc
    if not_before > now or not_after < now:
        raise TLSChainError("certificate is not currently valid")


def verify_chain(certificates: Sequence[Any], *, now: Optional[datetime] = None) -> None:
    """Verify signatures, validity and basic constraints of an ordered chain."""
    x509 = _x509()
    if not certificates:
        raise TLSChainError("certificate chain is empty")
    moment = now or datetime.now(timezone.utc)
    moment = _as_utc(moment)

    for cert in certificates:
        _check_validity(cert, moment)

    leaf = certificates[0]
    try:
        basic = leaf.extensions.get_extension_for_class(x509.BasicConstraints)
    except x509.ExtensionNotFound as exc:
        raise TLSChainError("leaf certificate has no BasicConstraints") from exc
    except (ValueError, TypeError) as exc:
        raise TLSChainError("leaf certificate extensions could not be parsed") from exc
    if basic.value.ca:
        raise TLSChainError("leaf certificate must not be a CA")

    for child, issuer in zip(certificates, certificates[1:]):
        try:
            child.verify_directly_issued_by(issuer)
        except Exception as exc:
            raise TLSChainError("certificate chain signature is invalid") from exc

    root = certificates[-1]
    if root.subject != root.issuer:
        raise TLSChainError("chain does not end in a self-signed trust anchor")
    try:
        root.verify_directly_issued_by(root)
    except Exception as exc:
        raise TLSChainError("trust anchor is not self-signed") from exc


def _dns_matches(pattern: str, hostname: str) -> bool:
    pattern = pattern.lower().rstrip(".")
    hostname = hostname.lower().rstrip(".")
    if "*" not in pattern:
        return pattern == hostname
    if not pattern.startswith("*.") or "*" in pattern[2:]:
        return False
    suffix = pattern[2:]
    label, separator, rest = hostname.partition(".")
    return bool(separator and label and rest == suffix)


def verify_hostname(cert: Any, hostname: Any) -> str:
    """Require the certificate SAN to match ``hostname``; no CN fallback."""
    x509 = _x509()
    if not isinstance(hostname, str) or not hostname.strip():
        raise TLSChainError("hostname must be a non-empty string")
    host = hostname.strip().rstrip(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
        try:
            host = validate_hostname(host, field="hostname")
        except TLSConfigValidationError as exc:
            raise TLSChainError("hostname is malformed") from exc

    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound as exc:
        raise TLSChainError("certificate has no SubjectAlternativeName") from exc
    except (ValueError, TypeError) as exc:
        raise TLSChainError("certificate SAN could not be parsed") from exc

    if address is not None:
        for entry in san.value.get_values_for_type(x509.IPAddress):
            if entry == address:
                return str(address)
        raise TLSChainError("certificate SAN does not cover the requested IP")

    for entry in san.value.get_values_for_type(x509.DNSName):
        if _dns_matches(str(entry), host):
            return host
    raise TLSChainError("certificate SAN does not cover the requested hostname")


def verify_pin(cert: Any, pin: Any) -> None:
    """Verify the leaf's DER SHA-256 fingerprint against a configured pin."""
    from cryptography.hazmat.primitives import serialization

    try:
        expected = validate_fingerprint(pin, field="tls pin")
    except TLSConfigValidationError as exc:
        raise TLSChainError("certificate pin is malformed") from exc
    digest = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    if not hmac.compare_digest(digest, expected):
        raise TLSChainError("certificate pin does not match the leaf")


def validate_certificate_bundle(
    pem_bytes: bytes,
    *,
    hostname: Optional[str] = None,
    pins: Iterable[Any] = (),
    now: Optional[datetime] = None,
) -> ValidatedChain:
    """Parse, verify and return a certificate bundle, refusing anything invalid."""
    certificates = load_certificates(pem_bytes)
    verify_chain(certificates, now=now)
    leaf = certificates[0]
    if hostname:
        verify_hostname(leaf, hostname)
    for pin in pins:
        verify_pin(leaf, pin)
    return ValidatedChain(
        leaf=leaf,
        certificates=tuple(certificates),
        hostname=hostname,
    )


def validate_certificate_file(
    cert_path: str,
    *,
    hostname: Optional[str] = None,
    pins: Iterable[Any] = (),
) -> ValidatedChain:
    """Read a certificate file and validate it with fail-closed semantics."""
    try:
        with open(cert_path, "rb") as handle:
            data = handle.read(MAX_CERT_BYTES + 1)
    except OSError as exc:
        raise TLSChainError("certificate file could not be read") from exc
    return validate_certificate_bundle(data, hostname=hostname, pins=pins)


__all__ = [
    "MAX_CERT_BYTES",
    "MAX_CHAIN_CERTS",
    "TLSChainError",
    "ValidatedChain",
    "load_certificates",
    "validate_certificate_bundle",
    "validate_certificate_file",
    "verify_chain",
    "verify_hostname",
    "verify_pin",
]
