"""
HKDF (HMAC-based Key Derivation Function) wrapper.

This module provides a small, focused wrapper around ``cryptography``'s
HKDF primitive so the rest of the encryption code can derive per-message
keys without importing the cryptography internals directly.

The wire format for the channel ratchet uses HKDF-SHA256 with:

    salt    = interval identifier bytes
    ikm     = interval start key (32 bytes)
    info    = per-message nonce || message_id bytes || context tag
    length  = 32 bytes (AES-256-GCM key size)

The function is intentionally small and side-effect free; it does not
log, persist, or wrap any of the input material.
"""

from __future__ import annotations

from typing import Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def derive_key(
    ikm: bytes,
    *,
    salt: bytes,
    info: bytes,
    length: int = 32,
    hash_algorithm: Optional[hashes.HashAlgorithm] = None,
) -> bytes:
    """Derive a fixed-length key using HKDF.

    Args:
        ikm: Input keying material. Must be non-empty.
        salt: Optional salt value (binding the output to a context). The
            channel ratchet uses the interval identifier bytes here.
        info: Optional context and application-specific information. The
            channel ratchet uses
            ``nonce || message_id_bytes || context_tag``.
        length: Number of bytes to derive. Defaults to ``32`` which is
            the AES-256 key size used by the rest of the package.
        hash_algorithm: Optional override for the underlying hash. When
            ``None``, SHA-256 is used. SHA-256 is the only algorithm
            currently used by the channel ratchet and the default
            matches the AES-256-GCM key schedule.

    Returns:
        ``length`` bytes of derived keying material.

    Raises:
        ValueError: If ``length`` is not positive, if ``ikm`` is empty,
            or if the requested length exceeds the HKDF output limit
            (255 * hash_length, well over 8000 bytes for SHA-256).
    """
    if length <= 0:
        raise ValueError("length must be a positive integer")
    if not ikm:
        raise ValueError("ikm (input keying material) must be non-empty")
    if length > 255 * 32:
        raise ValueError("length exceeds HKDF maximum output for SHA-256")

    kdf = HKDF(
        algorithm=hash_algorithm or hashes.SHA256(),
        length=length,
        salt=salt or None,
        info=info or b"",
    )
    return kdf.derive(ikm)
