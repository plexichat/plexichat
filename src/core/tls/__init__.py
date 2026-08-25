"""
TLS module - Automatic self-signed certificate generation.

This module provides:
- Automatic generation of self-signed TLS certificates
- Certificate storage and management
- Server startup integration for HTTPS

Usage:
    from src.core import tls

    cert_path, key_path = tls.ensure_certificates()
"""

import ipaddress
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Optional, Tuple

import utils.config as config
import utils.logger as logger

try:
    import fcntl
except ImportError:
    fcntl = None

MAX_VALIDITY_DAYS = 3650
RENEWAL_WINDOW_DAYS = 7
_HOSTNAME_FORBIDDEN_CHARS = (" ", "\t", "\n", "\r", "\x00")


class TLSConfigError(RuntimeError):
    """Raised when TLS configuration is invalid, incomplete, or unusable."""


def get_default_cert_dir() -> Path:
    """Get the default certificate directory."""
    return Path.home() / ".plexichat" / "certs"


def _normalize_path(path: str) -> str:
    expanded = os.path.expanduser(str(path))
    return os.path.abspath(expanded)


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _validate_validity_days(days: Any) -> int:
    try:
        days_int = int(days)
    except (TypeError, ValueError):
        raise TLSConfigError(
            f"tls.cert_days must be an integer, got {days!r}"
        ) from None
    if days_int <= 0 or days_int > MAX_VALIDITY_DAYS:
        raise TLSConfigError(
            f"tls.cert_days must be between 1 and {MAX_VALIDITY_DAYS} days, "
            f"got {days_int}"
        )
    return days_int


def _validate_hostname(hostname: object) -> str:
    if not isinstance(hostname, str):
        raise TLSConfigError(
            f"tls.hostname must be a non-empty string, got {type(hostname).__name__}"
        )
    hostname = hostname.strip()
    if not hostname:
        raise TLSConfigError("tls.hostname must be a non-empty string")
    if len(hostname) > 253:
        raise TLSConfigError(
            f"tls.hostname exceeds maximum length of 253 characters: {hostname!r}"
        )
    for ch in _HOSTNAME_FORBIDDEN_CHARS:
        if ch in hostname:
            raise TLSConfigError(
                f"tls.hostname contains forbidden character {ch!r}: {hostname!r}"
            )
    return hostname


@contextmanager
def _renewal_lock(lock_path: str):
    if fcntl is None:
        yield
        return
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _restrict_private_key_permissions(key_path: str) -> None:
    if os.name == "nt":
        return
    try:
        os.chmod(key_path, 0o600)
    except OSError as e:
        logger.warning(f"Failed to restrict private key permissions to 0600: {e}")


def ensure_certificates(
    cert_path: Optional[str] = None,
    key_path: Optional[str] = None,
    validity_days: int = 365,
    hostname: str = "localhost",
    force_regenerate: bool = False,
) -> Tuple[str, str]:
    """
    Ensure TLS certificates exist, generating if necessary.

    Args:
        cert_path: Path to certificate file (default: ~/.plexichat/certs/server.crt)
        key_path: Path to private key file (default: ~/.plexichat/certs/server.key)
        validity_days: Certificate validity period in days
        hostname: Hostname for the certificate (overridden by tls.hostname config)
        force_regenerate: Force regeneration even if certs exist

    Returns:
        Tuple of (cert_path, key_path)
    """
    tls_config = config.get("tls", {}) or {}

    cert_dir = get_default_cert_dir()
    cert_dir.mkdir(parents=True, exist_ok=True)

    cert_source = cert_path or tls_config.get("cert_path")
    key_source = key_path or tls_config.get("key_path")
    cert_path = _normalize_path(
        cert_source
        if isinstance(cert_source, str) and cert_source
        else str(cert_dir / "server.crt")
    )
    key_path = _normalize_path(
        key_source
        if isinstance(key_source, str) and key_source
        else str(cert_dir / "server.key")
    )

    validity_days = _validate_validity_days(tls_config.get("cert_days", validity_days))
    hostname = _validate_hostname(tls_config.get("hostname", hostname))

    preserved_sans: List[str] = []

    if not force_regenerate and os.path.exists(cert_path) and os.path.exists(key_path):
        if _check_certificate_validity(cert_path):
            logger.info(f"Using existing TLS certificate: {cert_path}")
            return cert_path, key_path
        logger.warning("Existing certificate is expired or invalid, regenerating...")
        identity = _read_cert_identity(cert_path)
        if identity is not None:
            original_cn, preserved_sans = identity
            if original_cn:
                hostname = original_cn

    logger.info("Generating self-signed TLS certificate...")
    _generate_self_signed_cert(
        cert_path,
        key_path,
        validity_days,
        hostname,
        preserved_sans=preserved_sans,
    )

    logger.warning("=" * 60)
    logger.warning("SECURITY WARNING: Using self-signed TLS certificate!")
    logger.warning("This is suitable for development and testing only.")
    logger.warning("For production, use certificates from a trusted CA.")
    logger.warning(f"Certificate: {cert_path}")
    logger.warning(f"Private key: {key_path}")
    logger.warning("=" * 60)

    return cert_path, key_path


def _get_not_valid_after(cert) -> datetime:
    after_utc = getattr(cert, "not_valid_after_utc", None)
    if after_utc is not None:
        if after_utc.tzinfo is None:
            return after_utc.replace(tzinfo=timezone.utc)
        return after_utc
    after_naive = getattr(cert, "not_valid_after", None)
    if after_naive is None:
        raise ValueError("certificate has no expiry attribute")
    if after_naive.tzinfo is None:
        return after_naive.replace(tzinfo=timezone.utc)
    return after_naive


def _check_certificate_validity(cert_path: str) -> bool:
    """Check if a certificate file exists and is not expired."""
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend

        with open(cert_path, "rb") as f:
            cert_data = f.read()

        cert = x509.load_pem_x509_certificate(cert_data, default_backend())
        expires_at = _get_not_valid_after(cert)

        now = datetime.now(timezone.utc)
        if expires_at < now + timedelta(days=RENEWAL_WINDOW_DAYS):
            return False

        return True
    except (OSError, ValueError, TypeError, AttributeError) as e:
        logger.warning(f"Certificate validation failed for {cert_path}: {e}")
        return False


def _read_cert_identity(
    cert_path: str,
) -> Optional[Tuple[Optional[str], List[str]]]:
    try:
        from cryptography import x509

        with open(cert_path, "rb") as f:
            cert_data = f.read()
        cert = x509.load_pem_x509_certificate(cert_data)

        common_name = None
        cn_attrs = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
        if cn_attrs:
            common_name = str(cn_attrs[0].value)

        san_values: List[str] = []
        try:
            san_ext = cert.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            )
            for general_name in san_ext.value:
                san_values.append(str(general_name.value))
        except x509.ExtensionNotFound:
            pass

        return common_name, san_values
    except (OSError, ValueError, TypeError, AttributeError) as e:
        logger.debug(f"Could not read existing certificate identity: {e}")
        return None


def _build_san_entries(hostname: str, preserved_sans: List[str]):
    from cryptography import x509

    raw_values: List[str] = []
    raw_values.extend(preserved_sans)
    raw_values.extend([hostname, "localhost", "127.0.0.1", "::1"])

    seen = set()
    ordered: List[str] = []
    for value in raw_values:
        normalized = str(value).strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)

    entries = []
    for value in ordered:
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(value)))
        except ValueError:
            entries.append(x509.DNSName(value))
    return entries


def _atomic_write_private_key(target_path: str, pem_bytes: bytes) -> None:
    tmp_path = f"{target_path}.{os.getpid()}.tmp"
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(pem_bytes)
        os.replace(tmp_path, target_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    _restrict_private_key_permissions(target_path)


def _atomic_write_public_file(target_path: str, pem_bytes: bytes) -> None:
    tmp_path = f"{target_path}.{os.getpid()}.tmp"
    try:
        with open(tmp_path, "wb") as f:
            f.write(pem_bytes)
        os.replace(tmp_path, target_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _generate_self_signed_cert(
    cert_path: str,
    key_path: str,
    validity_days: int,
    hostname: str,
    preserved_sans: Optional[List[str]] = None,
) -> None:
    """Generate a self-signed leaf server certificate using cryptography."""
    try:
        from cryptography import x509
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        raise ImportError(
            "cryptography library is required for TLS certificate generation. "
            "Install with: pip install cryptography"
        )

    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )

    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Development"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Local"),
            x509.NameAttribute(NameOID.COMMON_NAME, hostname),
        ]
    )

    san_list = _build_san_entries(hostname, list(preserved_sans or []))

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=validity_days))
        .add_extension(
            x509.SubjectAlternativeName(san_list),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
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
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(private_key, hashes.SHA256(), default_backend())
    )

    _ensure_parent_dir(cert_path)
    _ensure_parent_dir(key_path)

    key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)

    lock_path = f"{key_path}.lock"
    with _renewal_lock(lock_path):
        _atomic_write_private_key(key_path, key_pem)
        _atomic_write_public_file(cert_path, cert_pem)

    logger.info(f"Generated self-signed certificate valid for {validity_days} days")


def get_ssl_context(cert_path: str, key_path: str):
    """
    Create an SSL context for use with uvicorn.

    Args:
        cert_path: Path to certificate file
        key_path: Path to private key file

    Returns:
        SSL context dictionary for uvicorn
    """
    cert_path = _normalize_path(cert_path)
    key_path = _normalize_path(key_path)
    if not os.path.exists(cert_path):
        raise FileNotFoundError(f"Certificate not found: {cert_path}")
    if not os.path.exists(key_path):
        raise FileNotFoundError(f"Private key not found: {key_path}")

    return {
        "ssl_certfile": cert_path,
        "ssl_keyfile": key_path,
    }


def is_tls_enabled() -> bool:
    """Check if TLS is enabled in configuration."""
    tls_config = config.get("tls", {})
    return tls_config.get("enabled", False) or tls_config.get(
        "auto_generate_self_signed", False
    )


def get_tls_config() -> dict:
    """Get TLS configuration for server startup.

    Raises:
        TLSConfigError: If TLS is enabled but misconfigured (missing paths,
            unreadable certificates). Startup must abort rather than silently
            degrade to plain HTTP while the operator believes HTTPS is on.
    """
    tls_config = config.get("tls", {}) or {}

    if not is_tls_enabled():
        return {}

    if tls_config.get("auto_generate_self_signed", False):
        cert_path, key_path = ensure_certificates()
    else:
        cert_path = tls_config.get("cert_path")
        key_path = tls_config.get("key_path")

        if not cert_path or not key_path:
            raise TLSConfigError(
                "TLS is enabled but cert_path/key_path are not configured; "
                "refusing to start without HTTPS"
            )

        cert_path = _normalize_path(cert_path)
        key_path = _normalize_path(key_path)

        if not os.path.isfile(cert_path):
            raise TLSConfigError(f"TLS certificate not found: {cert_path}")
        if not os.path.isfile(key_path):
            raise TLSConfigError(f"TLS private key not found: {key_path}")

    return get_ssl_context(cert_path, key_path)


__all__ = [
    "ensure_certificates",
    "get_ssl_context",
    "is_tls_enabled",
    "get_tls_config",
    "get_default_cert_dir",
    "TLSConfigError",
]
