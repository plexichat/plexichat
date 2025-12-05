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

import os
from pathlib import Path
from typing import Tuple, Optional
from datetime import datetime, timedelta

import utils.config as config
import utils.logger as logger


def get_default_cert_dir() -> Path:
    """Get the default certificate directory."""
    return Path.home() / ".plexichat" / "certs"


def ensure_certificates(
    cert_path: Optional[str] = None,
    key_path: Optional[str] = None,
    validity_days: int = 365,
    hostname: str = "localhost",
    force_regenerate: bool = False
) -> Tuple[str, str]:
    """
    Ensure TLS certificates exist, generating if necessary.
    
    Args:
        cert_path: Path to certificate file (default: ~/.plexichat/certs/server.crt)
        key_path: Path to private key file (default: ~/.plexichat/certs/server.key)
        validity_days: Certificate validity period in days
        hostname: Hostname for the certificate
        force_regenerate: Force regeneration even if certs exist
        
    Returns:
        Tuple of (cert_path, key_path)
    """
    # Get paths from config or use defaults
    tls_config = config.get("tls", {})
    
    cert_dir = get_default_cert_dir()
    cert_dir.mkdir(parents=True, exist_ok=True)
    
    cert_path = cert_path or tls_config.get("cert_path") or str(cert_dir / "server.crt")
    key_path = key_path or tls_config.get("key_path") or str(cert_dir / "server.key")
    validity_days = tls_config.get("cert_days", validity_days)

    # Check if certificates already exist and are valid
    if not force_regenerate and os.path.exists(cert_path) and os.path.exists(key_path):
        if _check_certificate_validity(cert_path):
            logger.info(f"Using existing TLS certificate: {cert_path}")
            return cert_path, key_path
        else:
            logger.warning("Existing certificate is expired or invalid, regenerating...")
    
    # Generate new certificates
    logger.info("Generating self-signed TLS certificate...")
    _generate_self_signed_cert(cert_path, key_path, validity_days, hostname)
    
    logger.warning("=" * 60)
    logger.warning("SECURITY WARNING: Using self-signed TLS certificate!")
    logger.warning("This is suitable for development and testing only.")
    logger.warning("For production, use certificates from a trusted CA.")
    logger.warning(f"Certificate: {cert_path}")
    logger.warning(f"Private key: {key_path}")
    logger.warning("=" * 60)
    
    return cert_path, key_path


def _check_certificate_validity(cert_path: str) -> bool:
    """Check if a certificate file exists and is not expired."""
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend
        
        with open(cert_path, "rb") as f:
            cert_data = f.read()
        
        cert = x509.load_pem_x509_certificate(cert_data, default_backend())
        
        # Check if certificate is expired or will expire within 7 days
        now = datetime.utcnow()
        if cert.not_valid_after_utc.replace(tzinfo=None) < now + timedelta(days=7):
            return False
        
        return True
    except Exception as e:
        logger.debug(f"Certificate validation failed: {e}")
        return False


def _generate_self_signed_cert(
    cert_path: str,
    key_path: str,
    validity_days: int,
    hostname: str
) -> None:
    """Generate a self-signed certificate using cryptography library."""
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        import ipaddress
    except ImportError:
        raise ImportError(
            "cryptography library is required for TLS certificate generation. "
            "Install with: pip install cryptography"
        )

    # Generate private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )
    
    # Build certificate subject
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Development"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "Local"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PlexiChat"),
        x509.NameAttribute(NameOID.COMMON_NAME, hostname),
    ])
    
    # Build Subject Alternative Names
    san_list = [
        x509.DNSName(hostname),
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        x509.IPAddress(ipaddress.IPv6Address("::1")),
    ]
    
    # Add additional hostnames if configured
    if hostname not in ["localhost", "127.0.0.1"]:
        try:
            san_list.append(x509.IPAddress(ipaddress.ip_address(hostname)))
        except ValueError:
            pass  # Not an IP address, already added as DNS name
    
    # Build certificate
    now = datetime.utcnow()
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
            x509.BasicConstraints(ca=True, path_length=0),
            critical=True,
        )
        .sign(private_key, hashes.SHA256(), default_backend())
    )
    
    # Ensure directory exists
    os.makedirs(os.path.dirname(cert_path), exist_ok=True)
    os.makedirs(os.path.dirname(key_path), exist_ok=True)
    
    # Write private key
    with open(key_path, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))
    
    # Set restrictive permissions on private key
    try:
        os.chmod(key_path, 0o600)
    except Exception:
        pass  # Windows doesn't support chmod
    
    # Write certificate
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    
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
    import ssl
    
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
    return tls_config.get("enabled", False) or tls_config.get("auto_generate_self_signed", False)


def get_tls_config() -> dict:
    """Get TLS configuration for server startup."""
    tls_config = config.get("tls", {})
    
    if not is_tls_enabled():
        return {}
    
    # Auto-generate if enabled
    if tls_config.get("auto_generate_self_signed", False):
        cert_path, key_path = ensure_certificates()
    else:
        cert_path = tls_config.get("cert_path")
        key_path = tls_config.get("key_path")
        
        if not cert_path or not key_path:
            logger.warning("TLS enabled but cert_path/key_path not configured")
            return {}
    
    return get_ssl_context(cert_path, key_path)


__all__ = [
    'ensure_certificates',
    'get_ssl_context',
    'is_tls_enabled',
    'get_tls_config',
    'get_default_cert_dir',
]
