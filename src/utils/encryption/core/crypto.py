"""
Encryption/decryption mixin - AES-256-GCM data encryption.

Part of the EncryptionManager composite class.
"""

import base64
import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import utils.logger as logger

from .protocol import EncryptionCoreProtocol


class CryptoMixin(EncryptionCoreProtocol):
    """Mixin providing data encryption and decryption."""

    def encrypt_data(
        self, data: str, key: Optional[bytes] = None, context: Optional[str] = None
    ) -> str:
        """
        Encrypt data using current keyring version or provided key, optionally binding to context (AAD).
        """
        if key is not None:
            if len(key) != 32:
                raise ValueError("Key must be 32 bytes")
            aesgcm = AESGCM(key)
            version = 0
        else:
            version, key = self.keyring.get_key()
            if key is None:
                raise ValueError("No encryption key available")
            aesgcm = AESGCM(key)

        nonce = os.urandom(12)
        aad = context.encode("utf-8") if context else None
        ciphertext = aesgcm.encrypt(nonce, data.encode("utf-8"), aad)
        combined = nonce + ciphertext
        encoded = base64.b64encode(combined).decode("utf-8")
        return f"ENC:{version}:{encoded}"

    def decrypt_data(
        self,
        encrypted_data: str,
        key: Optional[bytes] = None,
        context: Optional[str] = None,
    ) -> str:
        """Decrypt data using AES-256-GCM."""
        if encrypted_data == "":
            return ""

        if not encrypted_data.startswith("ENC:"):
            raise ValueError("Malformed encrypted data")

        parts = encrypted_data.split(":", 2)
        if len(parts) != 3 or not parts[1] or not parts[2]:
            raise ValueError("Malformed encrypted data")
        version = int(parts[1])
        data_to_decode = parts[2]

        if key is not None and len(key) != 32:
            raise ValueError("Key must be 32 bytes")

        if not key:
            _, key = self.keyring.get_key(version)
        if key is None:
            raise ValueError(f"No encryption key available for version {version}")

        aesgcm = AESGCM(key)
        try:
            combined = base64.b64decode(data_to_decode)
        except Exception as e:
            raise ValueError(f"Malformed encrypted data: {e}")
        if len(combined) < 28:
            raise ValueError("Malformed encrypted data")
        nonce = combined[:12]
        ciphertext = combined[12:]
        aad = context.encode("utf-8") if context else None

        try:
            plaintext = aesgcm.decrypt(nonce, ciphertext, aad)
            return plaintext.decode("utf-8")
        except Exception as e:
            logger.error(
                f"Decryption failed for version {version} with context {context}: {repr(e)}"
            )
            raise ValueError(
                "Integrity check failed: Data may have been tampered with."
            )
