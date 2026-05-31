"""
Password hashing mixin - Argon2id password operations.

Part of the EncryptionManager composite class.
"""

import hashlib
import os
from typing import Optional, Tuple

from .protocol import EncryptionCoreProtocol


class PasswordMixin(EncryptionCoreProtocol):
    """Mixin providing password hashing and key derivation."""

    def derive_key(
        self,
        password: str,
        salt: Optional[bytes] = None,
        iterations: int = 100_000,
        length: int = 32,
    ) -> Tuple[bytes, bytes]:
        """Derive a key from a password using PBKDF2-HMAC-SHA256."""
        if not password:
            raise ValueError("Empty password")
        if salt is None:
            salt = os.urandom(16)
        if len(salt) < 16:
            raise ValueError("Salt too short")
        if length <= 0:
            raise ValueError("Invalid key length")

        key = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations), dklen=int(length)
        )
        return key, salt

    def hash_password(self, password: str) -> str:
        """Hash a password using Argon2id."""
        if not password:
            raise ValueError("Empty password")
        return self.password_hasher.hash(password)

    def verify_password(self, password: str, hash_str: str) -> bool:
        """Verify a password against its Argon2id hash."""
        from ..core.manager import VerifyMismatchError, VerificationError, InvalidHash

        try:
            self.password_hasher.verify(hash_str, password)
            return True
        except (VerifyMismatchError, VerificationError, InvalidHash):
            return False
