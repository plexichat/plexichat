"""
Protocol class for EncryptionManager mixins.

Provides type annotations for shared attributes and cross-mixin method references.
"""

from typing import Any


class EncryptionCoreProtocol:
    """Protocol for EncryptionManager mixin classes."""

    password_hasher: Any = None
    keyring: Any = None  # Keyring instance
    _argon2_hash_length: int = 32
    _argon2_salt_length: int = 16
