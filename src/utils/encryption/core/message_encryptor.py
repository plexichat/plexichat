"""
Message at-rest encryption using AES-256-GCM.

Provides per-message encryption with key versioning support
and automatic identification of encrypted vs plaintext content.
"""

import base64
import os
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .keyring import Keyring


class MessageEncryptor:
    """
    Handles message-at-rest encryption using AES-256-GCM.
    Uses a dedicated message keyring with its own KEK.
    """

    def __init__(self, keyring: Optional[Keyring] = None):
        self.keyring = keyring or Keyring(
            Path.home() / ".plexichat" / "data" / "message_keyring.json",
            kek_env_var="PLEXICHAT_MESSAGE_KEY",
        )

    def encrypt_message(self, content: str, message_id: Optional[int] = None) -> str:
        """Encrypt message content using AES-256-GCM."""
        version, key = self.keyring.get_key()
        aesgcm = AESGCM(key)
        nonce = os.urandom(12)
        aad = str(message_id).encode() if message_id else None
        ciphertext = aesgcm.encrypt(nonce, content.encode("utf-8"), aad)
        combined = nonce + ciphertext
        return f"ENC:{version}:{base64.b64encode(combined).decode('utf-8')}"

    def decrypt_message(
        self, encrypted_content: str, message_id: Optional[int] = None
    ) -> str:
        """Decrypt message content, handling non-encrypted (legacy) content."""
        if not encrypted_content.startswith("ENC:"):
            return encrypted_content
        try:
            parts = encrypted_content.split(":", 2)
            version = int(parts[1])
            _, key = self.keyring.get_key(version)
            aesgcm = AESGCM(key)
            combined = base64.b64decode(parts[2])
            nonce, ciphertext = combined[:12], combined[12:]
            aad = str(message_id).encode() if message_id else None
            return aesgcm.decrypt(nonce, ciphertext, aad).decode("utf-8")
        except Exception:
            raise ValueError("Decryption failed")

    def is_encrypted(self, content: str) -> bool:
        """Check if content has the encryption prefix."""
        return content.startswith("ENC:")

    def is_key_auto_generated(self) -> bool:
        """Check if the message encryption key was auto-generated."""
        return self.keyring.current_key_source == "generated"
