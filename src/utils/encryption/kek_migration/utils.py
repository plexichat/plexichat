"""Utility functions for KEK migration."""

import base64
from pathlib import Path
from typing import List


def decode_env_key(env_value: str) -> bytes:
    """
    Decode environment variable key (supports hex and Base64).

    Tries hex first (the standard Plexichat production format: 64-char hex = 32 bytes),
    then falls back to Base64. This order is important because a 64-char hex string
    decodes to 48 bytes in Base64 — it passes the length check incorrectly when
    Base64 is tried first.

    Args:
        env_value: The environment variable value

    Returns:
        The decoded 32-byte key

    Raises:
        ValueError: If the key cannot be decoded or is not 32 bytes
    """
    # Try hex first (standard Plexichat production format: 64 hex chars = 32 bytes)
    try:
        key = bytes.fromhex(env_value)
        if len(key) == 32:
            return key
    except Exception:
        pass

    # Try Base64 (alternative format)
    try:
        key = base64.b64decode(env_value)
        if len(key) == 32:
            return key
    except Exception:
        pass

    raise ValueError(
        f"Environment variable must be a 32-byte key (hex or Base64 encoded), got {len(env_value)} characters"
    )


def get_keyring_paths() -> List[Path]:
    """Get all keyring file paths."""
    data_dir = Path.home() / ".plexichat" / "data"
    keyring_files = [
        data_dir / "system_keyring.json",
        data_dir / "message_keyring.json",
        data_dir / "file_keyring.json",
    ]

    return [f for f in keyring_files if f.exists()]
