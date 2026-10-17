"""
Protocol class for EncryptionManager mixins.

Provides type annotations for shared attributes and cross-mixin method references.
"""

from typing import Any, Optional


class EncryptionCoreProtocol:
    """Protocol for EncryptionManager mixin classes."""

    password_hasher: Any = None
    keyring: Any = None  # Keyring instance
    _argon2_hash_length: int = 32
    _argon2_salt_length: int = 16

    def needs_rehash(self, hash_str: str) -> bool: ...
    def get_password_pepper_id(self, hash_str: str) -> Optional[str]: ...
