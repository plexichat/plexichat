import base64
import hashlib
import os
import time
import threading
from typing import Optional, Tuple
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHash
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey
)
from cryptography.hazmat.primitives import serialization

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
        self.default_key = None
    
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
    
    def _get_or_create_key(self, key: Optional[bytes] = None) -> bytes:
        """
        Get provided key or create/retrieve default key.
        
        Args:
            key (bytes, optional): Encryption key.
            
        Returns:
            bytes: The encryption key.
        """
        if key is not None:
            if len(key) != 32:
                raise ValueError("Key must be 32 bytes for AES-256")
            return key
        
        if self.default_key is None:
            self.default_key = AESGCM.generate_key(bit_length=256)
        
        return self.default_key
    
    def encrypt_data(self, data: str, key: Optional[bytes] = None) -> str:
        """
        Encrypt data using AES-256-GCM.
        
        Args:
            data (str): The data to encrypt.
            key (bytes, optional): 32-byte encryption key.
            
        Returns:
            str: Base64-encoded encrypted data (nonce + ciphertext + tag).
        """
        encryption_key = self._get_or_create_key(key)
        aesgcm = AESGCM(encryption_key)
        
        nonce = os.urandom(12)
        
        ciphertext = aesgcm.encrypt(nonce, data.encode('utf-8'), None)
        
        combined = nonce + ciphertext
        
        return base64.b64encode(combined).decode('utf-8')
    
    def decrypt_data(self, encrypted_data: str, key: Optional[bytes] = None) -> str:
        """
        Decrypt data using AES-256-GCM.
        
        Args:
            encrypted_data (str): Base64-encoded encrypted data.
            key (bytes, optional): 32-byte decryption key.
            
        Returns:
            str: Decrypted data.
            
        Raises:
            ValueError: If data is malformed or authentication fails.
        """
        encryption_key = self._get_or_create_key(key)
        aesgcm = AESGCM(encryption_key)
        
        try:
            combined = base64.b64decode(encrypted_data.encode('utf-8'))
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
