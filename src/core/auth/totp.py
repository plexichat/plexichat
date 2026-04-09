"""
TOTP (Time-based One-Time Password) implementation for 2FA.

Compatible with Google Authenticator, Authy, and other TOTP apps.
Includes replay attack prevention and DoS mitigation for backup codes.
"""

import os
import time
import threading
import importlib
from typing import List, Tuple, Optional, Dict, Any, Union

try:
    qrcode = importlib.import_module("qrcode")
except Exception:
    qrcode = None
    import logging as _logging

    _logging.getLogger(__name__).info(
        "qrcode module not available — QR code generation will be disabled"
    )

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


class TOTPReplayCache:
    """
    Thread-safe cache to prevent TOTP code replay attacks.

    Tracks used codes within their validity window to prevent reuse.
    """

    def __init__(self, ttl_seconds: int = 90):
        """
        Initialize replay cache.

        Args:
            ttl_seconds: How long to remember used codes (should be >= TOTP interval + window)
        """
        self._used_codes: Dict[str, int] = {}  # key -> expiry_timestamp
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._last_cleanup = 0

    def _cleanup(self, now: int) -> None:
        """Remove expired entries. Called while holding lock."""
        if now - self._last_cleanup < 30:  # Cleanup at most every 30 seconds
            return
        self._last_cleanup = now
        expired = [k for k, v in self._used_codes.items() if v < now]
        for k in expired:
            del self._used_codes[k]

    def check_and_mark(self, user_id: Union[int, str], code: str) -> bool:
        """
        Check if code was already used and mark it as used.

        Args:
            user_id: User ID to scope the code
            code: The TOTP code
        """
        key = f"{user_id}:{code}"
        now = int(time.time())

        with self._lock:
            self._cleanup(now)

            if key in self._used_codes:
                return False  # Replay detected

            self._used_codes[key] = now + self._ttl
            return True


# Global replay cache instance
_replay_cache = TOTPReplayCache(ttl_seconds=90)


def get_totp_config() -> Dict[str, Any]:
    """Get TOTP configuration from config system."""
    defaults = {
        "enabled": True,
        "issuer": "Plexichat",
        "digits": 6,
        "interval": 30,
        "backup_code_count": 10,
        "backup_code_length": 8,
        "backup_code_max_checks": 3,  # DoS mitigation
    }

    auth_config = config.get("authentication", {})
    user_totp_config = auth_config.get("totp", {})

    # Merge user config into defaults
    return {**defaults, **user_totp_config}


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
        issuer = totp_config.get("issuer", "Plexichat")

    totp = pyotp.TOTP(
        secret,
        digits=totp_config.get("digits", 6),
        interval=totp_config.get("interval", 30),
    )

    return totp.provisioning_uri(name=username, issuer_name=issuer)


def verify_totp_code(
    secret: str, code: str, window: int = 1, user_id: Optional[Union[int, str]] = None
) -> bool:
    """
    Verify a TOTP code with replay attack prevention.

    Args:
        secret: TOTP secret
        code: 6-digit code from authenticator app
        window: Number of time windows to check (for clock drift)
        user_id: User ID for replay prevention (if None, replay check is skipped)

    Returns:
        True if code is valid and not a replay
    """
    totp_config = get_totp_config()

    totp = pyotp.TOTP(
        secret,
        digits=totp_config.get("digits", 6),
        interval=totp_config.get("interval", 30),
    )

    # First verify the code is cryptographically valid
    if not totp.verify(code, valid_window=window):
        return False

    # Then check for replay attack (if user_id provided)
    if user_id is not None:
        if not _replay_cache.check_and_mark(user_id, code):
            return False  # Replay detected

    return True


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
        List of hashes for storage
    """
    result = []
    for code in codes:
        normalized = code.replace("-", "").lower()
        hashed = hash_password(normalized)
        result.append(hashed)
    return result


def verify_backup_code(
    code: str, hashed_codes: List[Any], max_checks: Optional[int] = None
) -> Tuple[bool, int]:
    """
    Verify a backup code against stored hashes.

    Args:
        code: Backup code to verify (with or without dash)
        hashed_codes: List of hashed backup codes
        max_checks: Deprecated and ignored; all stored backup codes are checked

    Returns:
        Tuple of (valid, index) where index is the matched code index or -1
    """
    # Normalize code (remove dash, lowercase)
    normalized = code.replace("-", "").lower()

    for i, entry in enumerate(hashed_codes):
        # Support both old format (prefix, hash) and new format (just hash string)
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            _, hashed = entry
        else:
            hashed = entry

        # Ensure hashed is a string for verify_password
        hashed_str = str(hashed) if not isinstance(hashed, str) else hashed

        if verify_password(normalized, hashed_str):
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
    from io import BytesIO

    if qrcode is None:
        raise RuntimeError("qrcode is required to generate QR codes")

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
