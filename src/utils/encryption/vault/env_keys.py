"""Environment KEK decoding."""

from __future__ import annotations

import base64

import utils.logger as logger


def decode_env_key(env_value: str, *, context: str) -> bytes:
    """Decode environment variable key (supports hex and Base64).

    Tries hex first (standard Plexichat format: 64 hex chars = 32 bytes),
    then Base64. Hex-first is important because a 64-char hex string
    decodes to 48 bytes in Base64.
    """
    from ..kek_validation import KekValidationError, validate_kek

    try:
        key = bytes.fromhex(env_value)
        if len(key) in (32, 48, 64):
            return validate_kek(key, source="env", context=context)
        logger.debug(
            "Environment variable %s decoded as hex but yielded %s bytes (expected 32-64)",
            context,
            len(key),
        )
    except Exception as exc:
        if isinstance(exc, KekValidationError):
            raise

    try:
        key = base64.b64decode(env_value)
        if len(key) in (32, 48, 64):
            return validate_kek(key, source="env", context=context)
        logger.debug(
            "Environment variable %s decoded as Base64 but yielded %s bytes (expected 32-64)",
            context,
            len(key),
        )
    except Exception as exc:
        if isinstance(exc, KekValidationError):
            raise

    raise ValueError(
        f"Environment variable {context} must be a 32-64 byte key (hex or Base64 encoded)"
    )
