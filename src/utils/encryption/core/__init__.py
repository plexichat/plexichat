"""
Encryption core module - Low-level encryption primitives.

This sub-package splits the original core.py into focused mixin modules.
The EncryptionManager is composed via MRO mixin pattern.
"""

from .keyring import Keyring, KeyringDecryptionError
from .manager import EncryptionManager, PasswordHasher
from .manager import VerifyMismatchError, VerificationError, InvalidHash
from .snowflake import SnowflakeGenerator
from .message_encryptor import MessageEncryptor
from .signing import generate_key_pair, sign_data, verify_signature

__all__ = [
    "Keyring",
    "KeyringDecryptionError",
    "EncryptionManager",
    "PasswordHasher",
    "VerifyMismatchError",
    "VerificationError",
    "InvalidHash",
    "SnowflakeGenerator",
    "MessageEncryptor",
    "generate_key_pair",
    "sign_data",
    "verify_signature",
]
