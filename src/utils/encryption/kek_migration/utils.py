"""Utility functions for KEK migration."""

import base64
import contextlib
from pathlib import Path
from typing import List


def decode_env_key(env_value: str, *, validate_quality: bool = False) -> bytes:
    """
    Decode environment variable key (supports hex and Base64).

    Tries hex first (the standard Plexichat production format: 64-char hex = 32 bytes),
    then falls back to Base64. This order is important because a 64-char hex string
    decodes to 48 bytes in Base64 — it passes the length check incorrectly when
    Base64 is tried first.

    Args:
        env_value: The environment variable value
        validate_quality: When True, enforce KEK strength/entropy/pattern rules
            (production-safe). Migration tooling may keep this False when working
            with legacy material under operator control.

    Returns:
        The decoded 32-byte key

    Raises:
        ValueError: If the key cannot be decoded or is not 32 bytes
    """
    decoded = None
    # Try hex first (standard Plexichat production format: 64 hex chars = 32 bytes)
    with contextlib.suppress(ValueError, TypeError):
        key = bytes.fromhex(env_value)
        if len(key) == 32:
            decoded = key

    # Try Base64 (alternative format)
    if decoded is None:
        with contextlib.suppress(ValueError, TypeError):
            key = base64.b64decode(env_value)
            if len(key) == 32:
                decoded = key

    if decoded is None:
        raise ValueError(
            f"Environment variable must be a 32-byte key (hex or Base64 encoded), got {len(env_value)} characters"
        )

    if validate_quality:
        from src.utils.encryption.kek_validation import validate_kek

        decoded = validate_kek(decoded, source="env", context="migration")
    return decoded


def get_keyring_paths() -> List[Path]:
    """Get all keyring file paths."""
    data_dir = Path.home() / ".plexichat" / "data"
    keyring_files = [
        data_dir / "system_keyring.json",
        data_dir / "message_keyring.json",
        data_dir / "file_keyring.json",
    ]

    return [f for f in keyring_files if f.exists()]
