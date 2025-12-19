import base64
import hashlib
import os
import time
import json
import threading
import utils.logger as logger
import utils.config as config
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHash
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey
)
from cryptography.hazmat.primitives import serialization

class Keyring:
    """
    Manages multiple versions of encryption keys for rotation support.
    Stored as a JSON file containing base64-encoded keys and rotation metadata.
    """
    def __init__(self, keyring_path: Path):
        self.path = keyring_path
        self.current_version: int = 0
        self.keys: Dict[int, bytes] = {}
        self.rotated_at: int = 0
        self._lock = threading.Lock()
        self.load()

    def load(self):
        """Load keyring from disk."""
        with self._lock:
            if not self.path.exists():
                return

            try:
                with open(self.path, "r") as f:
                    data = json.load(f)
                
                self.current_version = data.get("current_version", 0)
                self.rotated_at = data.get("rotated_at", 0)
                self.keys = {
                    int(v): base64.b64decode(k) 
                    for v, k in data.get("keys", {}).items()
                }
            except Exception as e:
                logger.error(f"Failed to load keyring from {self.path}: {e}")

    def save(self):
        """Save keyring to disk."""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "current_version": self.current_version,
                "rotated_at": self.rotated_at,
                "keys": {
                    str(v): base64.b64encode(k).decode('utf-8') 
                    for v, k in self.keys.items()
                }
            }
            # Use temporary file for atomic write
            temp_path = self.path.with_suffix(".tmp")
            with open(temp_path, "w") as f:
                json.dump(data, f, indent=2)
            
            if os.path.exists(self.path):
                os.remove(self.path)
            os.rename(temp_path, self.path)

    def get_key(self, version: Optional[int] = None) -> Tuple[int, bytes]:
        """Get a specific key version or the current one."""
        with self._lock:
            if not self.keys:
                # Generate initial key
                new_key = AESGCM.generate_key(bit_length=256)
                self.current_version = 1
                self.keys[1] = new_key
                self.rotated_at = int(time.time())
                # Save immediately since this is a new keyring
                self.path.parent.mkdir(parents=True, exist_ok=True)
                data = {
                    "current_version": self.current_version,
                    "rotated_at": self.rotated_at,
                    "keys": {
                        "1": base64.b64encode(new_key).decode('utf-8')
                    }
                }
                with open(self.path, "w") as f:
                    json.dump(data, f, indent=2)
            
            if version is None or version == 0:
                version = self.current_version
            
            key = self.keys.get(version)
            if not key:
                if version == self.current_version:
                    raise RuntimeError("No keys available in keyring")
                raise ValueError(f"Key version {version} not found in keyring")
            
            return version, key

    def rotate(self) -> int:
        """Generate a new key version and make it current."""
        with self._lock:
            new_version = self.current_version + 1
            new_key = AESGCM.generate_key(bit_length=256)
            self.keys[new_version] = new_key
            self.current_version = new_version
            self.rotated_at = int(time.time())
            logger.info(f"Rotated encryption key to version {new_version}")
        
        self.save()
        return self.current_version

class EncryptionManager:
    """
    Manager for various encryption operations using future-proof standards.
    """
    def __init__(
        self,
        argon2_time_cost: int = 2,
        argon2_memory_cost: int = 65536,
        argon2_parallelism: int = 2,
        argon2_hash_length: int = 32,
        argon2_salt_length: int = 16
    ):
        """
        Initialize the encryption manager.
        
        Args:
            argon2_time_cost (int): Time cost parameter for Argon2.
            argon2_memory_cost (int): Memory cost in KiB for Argon2.
            argon2_parallelism (int): Parallelism factor for Argon2.
            argon2_hash_length (int): Length of hash output in bytes.
            argon2_salt_length (int): Length of salt in bytes.
        """
        self.password_hasher = PasswordHasher(
            time_cost=argon2_time_cost,
            memory_cost=argon2_memory_cost,
            parallelism=argon2_parallelism,
            hash_len=argon2_hash_length,
            salt_len=argon2_salt_length
        )
        self.keyring = Keyring(Path.home() / ".plexichat" / "data" / "keyring.json")
        self._ensure_initial_key()

    def _ensure_initial_key(self):
        """Ensure at least one key exists in the keyring."""
        if not self.keyring.keys:
            # Check for legacy key file first
            legacy_path = Path.home() / ".plexichat" / "data" / ".encryption_key"
            if legacy_path.exists():
                try:
                    with open(legacy_path, "rb") as f:
                        key = f.read()
                    if len(key) == 32:
                        self.keyring.keys[1] = key
                        self.keyring.current_version = 1
                        self.keyring.rotated_at = int(legacy_path.stat().st_mtime)
                        self.keyring.save()
                        logger.info("Migrated legacy encryption key to keyring version 1")
                except Exception as e:
                    logger.error(f"Failed to migrate legacy key: {e}")

            # Still no keys? Generate new one
            if not self.keyring.keys:
                self.keyring.get_key() # Triggers generation
                self.keyring.save()

    def rotate_keys(self, force: bool = False) -> bool:
        """
        Check if keys need rotation based on config and rotate if needed.
        
        Args:
            force (bool): Rotate regardless of time elapsed.
            
        Returns:
            bool: True if rotation occurred.
        """
        rotation_days = config.get("encryption", {}).get("key_rotation_days", 90)
        if rotation_days <= 0 and not force:
            return False

        now = int(time.time())
        age_seconds = now - self.keyring.rotated_at
        if force or age_seconds >= (rotation_days * 86400):
            self.keyring.rotate()
            return True
        
        return False

    def hash_password(self, password: str) -> str:
        """
        Hash a password using Argon2id (recommended by OWASP).
        
        Args:
            password (str): The password to hash.
            
        Returns:
            str: The hashed password in PHC string format.
            
        Raises:
            ValueError: If password is empty.
        """
        if not password:
            raise ValueError("Password cannot be empty")

        return self.password_hasher.hash(password)

    def verify_password(self, password: str, hash_str: str) -> bool:
        """
        Verify a password against its Argon2 hash.
        
        Args:
            password (str): The password to verify.
            hash_str (str): The hash to verify against.
            
        Returns:
            bool: True if password matches, False otherwise.
        """
        try:
            self.password_hasher.verify(hash_str, password)

            if self.password_hasher.check_needs_rehash(hash_str):
                pass

            return True
        except (VerifyMismatchError, VerificationError, InvalidHash):
            return False

    def encrypt_data(self, data: str, key: Optional[bytes] = None) -> str:
        """
        Encrypt data using AES-256-GCM.
        
        Args:
            data (str): The data to encrypt.
            key (bytes, optional): 32-byte encryption key. If None, uses current keyring key.
            
        Returns:
            str: Base64-encoded encrypted data with version prefix (ENC:V:<base64>).
        """
        if key is not None:
            if len(key) != 32:
                raise ValueError("Key must be 32 bytes for AES-256")
            encryption_key = key
            version_prefix = "" # No prefix for custom keys to maintain compatibility
        else:
            version, encryption_key = self.keyring.get_key()
            version_prefix = f"ENC:{version}:"

        aesgcm = AESGCM(encryption_key)
        nonce = os.urandom(12)
        ciphertext = aesgcm.encrypt(nonce, data.encode('utf-8'), None)
        combined = nonce + ciphertext
        encoded = base64.b64encode(combined).decode('utf-8')
        
        return version_prefix + encoded

    def decrypt_data(self, encrypted_data: str, key: Optional[bytes] = None) -> str:
        """
        Decrypt data using AES-256-GCM.
        
        Args:
            encrypted_data (str): Encrypted data (with or without prefix).
            key (bytes, optional): 32-byte decryption key.
            
        Returns:
            str: Decrypted data.
        """
        if not encrypted_data:
            return ""

        # Parse version prefix
        version = None
        data_to_decode = encrypted_data
        
        if encrypted_data.startswith("ENC:"):
            parts = encrypted_data.split(":", 2)
            if len(parts) == 3:
                try:
                    version = int(parts[1])
                    data_to_decode = parts[2]
                except ValueError:
                    pass

        if key is not None:
            if len(key) != 32:
                raise ValueError("Key must be 32 bytes")
            decryption_key = key
        else:
            _, decryption_key = self.keyring.get_key(version)

        aesgcm = AESGCM(decryption_key)

        try:
            combined = base64.b64decode(data_to_decode.encode('utf-8'))
        except Exception as e:
            raise ValueError(f"Invalid base64 encoding: {e}")

        if len(combined) < 28:
            raise ValueError("Encrypted data too short")

        nonce = combined[:12]
        ciphertext = combined[12:]

        try:
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
            return plaintext.decode('utf-8')
        except Exception as e:
            raise ValueError(f"Decryption failed: {e}")

    def derive_key(self, password: str, salt: Optional[bytes] = None) -> Tuple[bytes, bytes]:
        """
        Derive a 256-bit key from a password using PBKDF2-HMAC-SHA256.
        
        Args:
            password (str): The password to derive from.
            salt (bytes, optional): Salt for derivation. Generated if not provided.
            
        Returns:
            Tuple[bytes, bytes]: (derived_key, salt)
        """
        if salt is None:
            salt = os.urandom(16)
        elif len(salt) < 16:
            raise ValueError("Salt must be at least 16 bytes")

        key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)

        return key, salt


def generate_key_pair() -> Tuple[bytes, bytes]:
    """
    Generate an Ed25519 key pair for digital signatures.
    
    Returns:
        Tuple[bytes, bytes]: (private_key_bytes, public_key_bytes)
    """
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption()
    )

    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    )

    return private_bytes, public_bytes


def sign_data(data: bytes, private_key: bytes) -> bytes:
    """
    Sign data using Ed25519.
    
    Args:
        data (bytes): Data to sign.
        private_key (bytes): 32-byte Ed25519 private key.
        
    Returns:
        bytes: 64-byte signature.
    """
    key = Ed25519PrivateKey.from_private_bytes(private_key)
    signature = key.sign(data)
    return signature


def verify_signature(data: bytes, signature: bytes, public_key: bytes) -> bool:
    """
    Verify a signature using Ed25519.
    
    Args:
        data (bytes): Original data.
        signature (bytes): Signature to verify.
        public_key (bytes): 32-byte Ed25519 public key.
        
    Returns:
        bool: True if signature is valid, False otherwise.
    """
    try:
        key = Ed25519PublicKey.from_public_bytes(public_key)
        key.verify(signature, data)
        return True
    except Exception:
        return False


class MessageEncryptor:
    """
    Dedicated encryptor for message content at rest.
    Uses AES-256-GCM with a versioned keyring.
    Optimized for high-throughput message encryption/decryption.
    """
    
    # Prefix to identify encrypted messages (allows legacy plaintext detection)
    ENCRYPTED_PREFIX = "ENC:"  # New prefix format: ENC:<version>:<base64>
    LEGACY_PREFIX = "ENC:1:"   # Old prefix format
    
    def __init__(self, keyring: Optional[Keyring] = None):
        """
        Initialize the message encryptor.
        
        Args:
            keyring: Optional Keyring instance. Defaults to shared keyring.
        """
        self.keyring = keyring or Keyring(Path.home() / ".plexichat" / "data" / "keyring.json")
        self._key_is_auto_generated = False
        self._ensure_initial_key()

    def _ensure_initial_key(self):
        """Ensure at least one key exists in the keyring, migrating legacy message key if needed."""
        if not self.keyring.keys:
            # Check for legacy message key file
            legacy_path = Path.home() / ".plexichat" / "data" / ".message_encryption_key"
            if legacy_path.exists():
                try:
                    with open(legacy_path, "rb") as f:
                        key = f.read()
                    if len(key) == 32:
                        self.keyring.keys[1] = key
                        self.keyring.current_version = 1
                        self.keyring.rotated_at = int(legacy_path.stat().st_mtime)
                        self.keyring.save()
                        logger.info("Migrated legacy message encryption key to keyring version 1")
                except Exception as e:
                    logger.error(f"Failed to migrate legacy message key: {e}")

            # Still no keys? Generate new one
            if not self.keyring.keys:
                self.keyring.get_key() # Triggers generation
                self.keyring.save()
                self._key_is_auto_generated = True

    def is_key_auto_generated(self) -> bool:
        """Check if the encryption key was auto-generated."""
        return self._key_is_auto_generated
    
    def encrypt_message(self, content: str, message_id: Optional[int] = None) -> str:
        """
        Encrypt message content.
        
        Args:
            content: The plaintext message content.
            message_id: Optional message ID to include in AAD for integrity.
            
        Returns:
            Encrypted content with versioned prefix (ENC:<version>:<base64>).
        """
        if not content:
            return content
        
        version, key = self.keyring.get_key()
        cipher = AESGCM(key)
        
        nonce = os.urandom(12)
        aad = str(message_id).encode('utf-8') if message_id else None
        
        ciphertext = cipher.encrypt(nonce, content.encode('utf-8'), aad)
        combined = nonce + ciphertext
        
        return f"ENC:{version}:" + base64.b64encode(combined).decode('utf-8')
    
    def decrypt_message(self, encrypted_content: str, message_id: Optional[int] = None) -> str:
        """
        Decrypt message content.
        
        Args:
            encrypted_content: The encrypted content (with or without prefix).
            message_id: Optional message ID used as AAD during encryption.
            
        Returns:
            Decrypted plaintext content.
        """
        if not encrypted_content:
            return encrypted_content
        
        # Check for encryption prefix
        if not encrypted_content.startswith(self.ENCRYPTED_PREFIX):
            return encrypted_content
        
        parts = encrypted_content.split(":", 2)
        if len(parts) != 3:
            # Try to handle potential legacy "ENC:1:<base64>" where it was treated as "ENC:1" prefix
            if encrypted_content.startswith(self.LEGACY_PREFIX):
                version = 1
                data = encrypted_content[len(self.LEGACY_PREFIX):]
            else:
                return encrypted_content
        else:
            try:
                version = int(parts[1])
                data = parts[2]
            except ValueError:
                return encrypted_content
        
        try:
            _, key = self.keyring.get_key(version)
        except (ValueError, RuntimeError) as e:
            logger.error(f"Decryption failed: {e}")
            raise ValueError(f"Unknown key version: {version}")

        cipher = AESGCM(key)
        
        try:
            combined = base64.b64decode(data.encode('utf-8'))
        except Exception as e:
            raise ValueError(f"Invalid base64 encoding: {e}")
        
        if len(combined) < 28:
            raise ValueError("Encrypted data too short")
        
        nonce = combined[:12]
        ciphertext = combined[12:]
        aad = str(message_id).encode('utf-8') if message_id else None
        
        try:
            plaintext = cipher.decrypt(nonce, ciphertext, aad)
            return plaintext.decode('utf-8')
        except Exception as e:
            raise ValueError(f"Decryption failed: {e}")
    
    def is_encrypted(self, content: str) -> bool:
        """Check if content is encrypted."""
        return content.startswith(self.ENCRYPTED_PREFIX) if content else False


class SnowflakeGenerator:
    """
    Twitter-style Snowflake ID generator for distributed unique IDs.
    
    Snowflake IDs are 64-bit integers composed of:
    - 41 bits: Timestamp in milliseconds since epoch
    - 5 bits: Datacenter ID
    - 5 bits: Worker ID
    - 12 bits: Sequence number
    """

    TIMESTAMP_BITS = 41
    DATACENTER_BITS = 5
    WORKER_BITS = 5
    SEQUENCE_BITS = 12

    MAX_DATACENTER_ID = (1 << DATACENTER_BITS) - 1
    MAX_WORKER_ID = (1 << WORKER_BITS) - 1
    MAX_SEQUENCE = (1 << SEQUENCE_BITS) - 1

    TIMESTAMP_SHIFT = DATACENTER_BITS + WORKER_BITS + SEQUENCE_BITS
    DATACENTER_SHIFT = WORKER_BITS + SEQUENCE_BITS
    WORKER_SHIFT = SEQUENCE_BITS

    def __init__(
        self,
        worker_id: int = 1,
        datacenter_id: int = 1,
        epoch_timestamp: Optional[int] = None
    ):
        """
        Initialize the Snowflake generator.
        
        Args:
            worker_id (int): Worker ID (0-31).
            datacenter_id (int): Datacenter ID (0-31).
            epoch_timestamp (int, optional): Custom epoch in milliseconds.
                                            Defaults to 2024-01-01 00:00:00 UTC.
        """
        if worker_id < 0 or worker_id > self.MAX_WORKER_ID:
            raise ValueError(f"Worker ID must be between 0 and {self.MAX_WORKER_ID}")

        if datacenter_id < 0 or datacenter_id > self.MAX_DATACENTER_ID:
            raise ValueError(f"Datacenter ID must be between 0 and {self.MAX_DATACENTER_ID}")

        self.worker_id = worker_id
        self.datacenter_id = datacenter_id

        if epoch_timestamp is None:
            self.epoch = int(time.mktime(time.strptime("2024-01-01 00:00:00", "%Y-%m-%d %H:%M:%S")) * 1000)
        else:
            self.epoch = epoch_timestamp

        self.sequence = 0
        self.last_timestamp = -1
        self.lock = threading.Lock()

    def _current_timestamp(self) -> int:
        """Get current timestamp in milliseconds."""
        return int(time.time() * 1000)

    def _wait_next_millis(self, last_timestamp: int) -> int:
        """Wait until next millisecond."""
        timestamp = self._current_timestamp()
        while timestamp <= last_timestamp:
            timestamp = self._current_timestamp()
        return timestamp

    def generate(self) -> int:
        """
        Generate a unique Snowflake ID.
        
        Returns:
            int: 64-bit unique identifier.
            
        Raises:
            RuntimeError: If clock moves backwards.
        """
        with self.lock:
            timestamp = self._current_timestamp()

            if timestamp < self.last_timestamp:
                raise RuntimeError(
                    f"Clock moved backwards. Refusing to generate ID for {self.last_timestamp - timestamp} ms"
                )

            if timestamp == self.last_timestamp:
                self.sequence = (self.sequence + 1) & self.MAX_SEQUENCE
                if self.sequence == 0:
                    timestamp = self._wait_next_millis(self.last_timestamp)
            else:
                self.sequence = 0

            self.last_timestamp = timestamp

            snowflake_id = (
                ((timestamp - self.epoch) << self.TIMESTAMP_SHIFT) |
                (self.datacenter_id << self.DATACENTER_SHIFT) |
                (self.worker_id << self.WORKER_SHIFT) |
                self.sequence
            )

            return snowflake_id

    def parse(self, snowflake_id: int) -> dict:
        """
        Parse a Snowflake ID into its components.
        
        Args:
            snowflake_id (int): The Snowflake ID to parse.
            
        Returns:
            dict: Dictionary with timestamp, datacenter_id, worker_id, and sequence.
        """
        timestamp_ms = (snowflake_id >> self.TIMESTAMP_SHIFT) + self.epoch
        datacenter_id = (snowflake_id >> self.DATACENTER_SHIFT) & self.MAX_DATACENTER_ID
        worker_id = (snowflake_id >> self.WORKER_SHIFT) & self.MAX_WORKER_ID
        sequence = snowflake_id & self.MAX_SEQUENCE

        return {
            'timestamp': timestamp_ms,
            'datacenter_id': datacenter_id,
            'worker_id': worker_id,
            'sequence': sequence
        }
