"""
TOTP (Time-based One-Time Password) implementation for 2FA.

Compatible with Google Authenticator, Authy, and other TOTP apps.
"""

import os
from typing import List, Tuple, Optional, Dict, Any

import utils.config as config

# Import encryption for storing secrets
from src.utils.encryption import (
    encrypt_data,
    decrypt_data,
    hash_password,
    verify_password,
)

# pyotp for TOTP
import pyotp


def get_totp_config() -> Dict[str, Any]:
    """Get TOTP configuration from config system."""
    auth_config = config.get("authentication", {})
    return auth_config.get(
        "totp",
        {
            "enabled": True,
            "issuer": "PlexiChat",
            "digits": 6,
            "interval": 30,
            "backup_code_count": 10,
            "backup_code_length": 8,
        },
    )


def generate_totp_secret() -> str:
    """
    Generate a new TOTP secret.

    Returns:
        Base32-encoded secret string
    """
    return pyotp.random_base32()


def encrypt_totp_secret(secret: str) -> str:
    """
    Encrypt a TOTP secret for database storage.

    Args:
        secret: Plain TOTP secret

    Returns:
        Encrypted secret string
    """
    return encrypt_data(secret)


def decrypt_totp_secret(encrypted_secret: str) -> str:
    """
    Decrypt a TOTP secret from database.

    Args:
        encrypted_secret: Encrypted secret string

    Returns:
        Plain TOTP secret
    """
    return decrypt_data(encrypted_secret)


def generate_totp_uri(secret: str, username: str, issuer: Optional[str] = None) -> str:
    """
    Generate a TOTP provisioning URI for QR codes.

    Args:
        secret: TOTP secret
        username: User's username or email
        issuer: App name (defaults to config)

    Returns:
        otpauth:// URI string
    """
    totp_config = get_totp_config()
    if issuer is None:
        issuer = totp_config.get("issuer", "PlexiChat")

    totp = pyotp.TOTP(
        secret,
        digits=totp_config.get("digits", 6),
        interval=totp_config.get("interval", 30),
    )

    return totp.provisioning_uri(name=username, issuer_name=issuer)


def verify_totp_code(secret: str, code: str, window: int = 1) -> bool:
    """
    Verify a TOTP code.

    Args:
        secret: TOTP secret
        code: 6-digit code from authenticator app
        window: Number of time windows to check (for clock drift)

    Returns:
        True if code is valid
    """
    totp_config = get_totp_config()

    totp = pyotp.TOTP(
        secret,
        digits=totp_config.get("digits", 6),
        interval=totp_config.get("interval", 30),
    )

    # valid_window allows for clock drift
    return totp.verify(code, valid_window=window)


def generate_backup_codes(count: Optional[int] = None) -> List[str]:
    """
    Generate backup codes for 2FA recovery.

    Args:
        count: Number of codes to generate (defaults to config)

    Returns:
        List of backup codes in XXXX-XXXX format
    """
    totp_config = get_totp_config()
    if count is None:
        count = int(totp_config.get("backup_code_count", 10))

    code_length = int(totp_config.get("backup_code_length", 8))
    half_length = code_length // 2

    codes = []
    for _ in range(count):
        # Generate random bytes and convert to hex
        random_bytes = os.urandom(half_length)
        code = random_bytes.hex().upper()
        # Format as XXXX-XXXX
        formatted = f"{code[:half_length]}-{code[half_length:]}"
        codes.append(formatted)

    return codes


def hash_backup_codes(codes: List[str]) -> List[str]:
    """
    Hash backup codes for storage.

    Args:
        codes: List of plain backup codes

    Returns:
        List of hashed codes
    """
    return [hash_password(code.replace("-", "").lower()) for code in codes]


def verify_backup_code(code: str, hashed_codes: List[str]) -> Tuple[bool, int]:
    """
    Verify a backup code against stored hashes.

    Args:
        code: Backup code to verify (with or without dash)
        hashed_codes: List of hashed backup codes

    Returns:
        Tuple of (valid, index) where index is the matched code index or -1
    """
    # Normalize code (remove dash, lowercase)
    normalized = code.replace("-", "").lower()

    for i, hashed in enumerate(hashed_codes):
        if verify_password(normalized, hashed):
            return True, i

    return False, -1


def generate_qr_code_data(uri: str) -> bytes:
    """
    Generate QR code image data for a TOTP URI.

    Args:
        uri: TOTP provisioning URI

    Returns:
        PNG image data as bytes
    """
    import qrcode
    from io import BytesIO

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(uri)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    buffer = BytesIO()
    img.save(buffer, "PNG")
    return buffer.getvalue()


def get_current_totp_code(secret: str) -> str:
    """
    Get the current TOTP code for a secret.
    Useful for testing.

    Args:
        secret: TOTP secret

    Returns:
        Current 6-digit code
    """
    totp_config = get_totp_config()

    totp = pyotp.TOTP(
        secret,
        digits=totp_config.get("digits", 6),
        interval=totp_config.get("interval", 30),
    )

    return totp.now()
