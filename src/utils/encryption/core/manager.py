"""
EncryptionManager - Composite class combining all encryption mixins.

Assembles password hashing, encryption/decryption, blind index,
and key rotation capabilities via MRO mixin pattern.
"""

import importlib


try:
    _argon2 = importlib.import_module("argon2")
    _argon2_exceptions = importlib.import_module("argon2.exceptions")
    PasswordHasher = _argon2.PasswordHasher
    VerifyMismatchError = _argon2_exceptions.VerifyMismatchError
    VerificationError = _argon2_exceptions.VerificationError
    InvalidHash = _argon2_exceptions.InvalidHash
except Exception:
    PasswordHasher = None
    VerifyMismatchError = Exception
    VerificationError = Exception
    InvalidHash = Exception

from pathlib import Path

from .password import PasswordMixin
from .crypto import CryptoMixin
from .blind_index import BlindIndexMixin
from .rotation import RotationMixin
from .keyring import Keyring


class EncryptionManager(
    PasswordMixin,
    CryptoMixin,
    BlindIndexMixin,
    RotationMixin,
):
    """
    Hardened Encryption Manager.
    Uses Argon2id for passwords and AES-256-GCM for data.

    Composed from mixins that provide:
    - Password hashing and verification (PasswordMixin)
    - Data encryption and decryption (CryptoMixin)
    - Blind index generation (BlindIndexMixin)
    - Key rotation (RotationMixin)
    """

    def __init__(
        self,
        argon2_time_cost=2,
        argon2_memory_cost=65536,
        argon2_parallelism=2,
        argon2_hash_length: int = 32,
        argon2_salt_length: int = 16,
    ):
        if PasswordHasher is None:
            raise RuntimeError("argon2 is required for password hashing")
        self._argon2_hash_length = int(argon2_hash_length)
        self._argon2_salt_length = int(argon2_salt_length)
        self.password_hasher = PasswordHasher(
            time_cost=argon2_time_cost,
            memory_cost=argon2_memory_cost,
            parallelism=argon2_parallelism,
            hash_len=self._argon2_hash_length,
            salt_len=self._argon2_salt_length,
        )
        self.keyring = Keyring(
            Path.home() / ".plexichat" / "data" / "system_keyring.json",
            kek_env_var="PLEXICHAT_SYSTEM_KEY",
        )
