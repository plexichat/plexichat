"""
Encryption utility module - Zero-friction encryption for Python applications.

Usage:
    # In main.py (setup once)
    import utils.encryption as encryption
    encryption.setup(worker_id=1)

    # In any other file (no setup needed)
    import utils.encryption as encryption

    # Password hashing
    hash_str = encryption.hash_password("my_password")
    is_valid = encryption.verify_password("my_password", hash_str)

    # Data encryption
    encrypted = encryption.encrypt_data("sensitive data")
    decrypted = encryption.decrypt_data(encrypted)

    # Snowflake IDs
    unique_id = encryption.generate_snowflake_id()

    # Digital signatures
    signature = encryption.sign_data(b"message", private_key)
    verified = encryption.verify_signature(b"message", signature, public_key)
"""

from typing import Optional, Tuple
from .core import (
    EncryptionManager,
    KeyringDecryptionError,
    SnowflakeGenerator,
    MessageEncryptor,
    generate_key_pair as _generate_key_pair,
    sign_data as _sign_data,
    verify_signature as _verify_signature,
)

_encryption_manager: Optional[EncryptionManager] = None
_snowflake_generator: Optional[SnowflakeGenerator] = None
_message_encryptor: Optional[MessageEncryptor] = None
_setup_called = False


def setup(
    worker_id: int = 1,
    datacenter_id: int = 1,
    epoch_timestamp: Optional[int] = None,
    argon2_time_cost: int = 2,
    argon2_memory_cost: int = 65536,
    argon2_parallelism: int = 2,
):
    """
    Setup the encryption utilities. Optional - uses defaults if not called.

    Args:
        worker_id (int): Worker ID for Snowflake generation (0-1023).
        datacenter_id (int): Datacenter ID for Snowflake generation (0-1023).
        epoch_timestamp (int): Custom epoch timestamp in milliseconds.
        argon2_time_cost (int): Time cost for Argon2 (iterations).
        argon2_memory_cost (int): Memory cost for Argon2 in KiB.
        argon2_parallelism (int): Parallelism factor for Argon2.
    """
    global _encryption_manager, _snowflake_generator, _setup_called

    _encryption_manager = EncryptionManager(
        argon2_time_cost=argon2_time_cost,
        argon2_memory_cost=argon2_memory_cost,
        argon2_parallelism=argon2_parallelism,
    )

    _snowflake_generator = SnowflakeGenerator(
        worker_id=worker_id,
        datacenter_id=datacenter_id,
        epoch_timestamp=epoch_timestamp,
    )

    _setup_called = True


def _get_manager() -> EncryptionManager:
    """Internal: Get or create encryption manager instance."""
    global _encryption_manager, _setup_called

    if not _setup_called:
        _encryption_manager = EncryptionManager()
        _setup_called = True

    if _encryption_manager is None:
        raise RuntimeError("Encryption manager could not be initialized.")
    return _encryption_manager


def _get_snowflake() -> SnowflakeGenerator:
    """Internal: Get or create snowflake generator instance."""
    global _snowflake_generator, _setup_called

    if _snowflake_generator is None:
        _snowflake_generator = SnowflakeGenerator()

    return _snowflake_generator


def _get_message_encryptor() -> MessageEncryptor:
    """Internal: Get or create message encryptor instance."""
    global _message_encryptor

    if _message_encryptor is None:
        _message_encryptor = MessageEncryptor()

    return _message_encryptor


def hash_password(password: str) -> str:
    """
    Hash a password using Argon2id.

    Args:
        password (str): The password to hash.

    Returns:
        str: The hashed password (includes salt and parameters).
    """
    return _get_manager().hash_password(password)


def verify_password(password: str, hash_str: str) -> bool:
    """
    Verify a password against its hash.

    Args:
        password (str): The password to verify.
        hash_str (str): The hash to verify against.

    Returns:
        bool: True if password matches, False otherwise.
    """
    return _get_manager().verify_password(password, hash_str)


def encrypt_data(
    data: str, key: Optional[bytes] = None, context: Optional[str] = None
) -> str:
    """
    Encrypt data using AES-256-GCM.

    Args:
        data (str): The data to encrypt.
        key (bytes, optional): Encryption key. If None, uses derived key.
        context (str, optional): Additional Authenticated Data (AAD) for integrity binding.

    Returns:
        str: Base64-encoded encrypted data with nonce and tag.
    """
    return _get_manager().encrypt_data(data, key, context)


def decrypt_data(
    encrypted_data: str, key: Optional[bytes] = None, context: Optional[str] = None
) -> str:
    """
    Decrypt data using AES-256-GCM.

    Args:
        encrypted_data (str): Base64-encoded encrypted data.
        key (bytes, optional): Decryption key. If None, uses derived key.
        context (str, optional): Additional Authenticated Data (AAD) used during encryption.

    Returns:
        str: Decrypted data.
    """
    return _get_manager().decrypt_data(encrypted_data, key, context)


def blind_index(data: str, scope: str) -> str:
    """
    Generate a keyed hash for searching encrypted fields.

    Args:
        data (str): The data to index.
        scope (str): The scope/context for the index.

    Returns:
        str: Hex-encoded blind index.
    """
    return _get_manager().blind_index(data, scope)


def fast_blind_index(data: str, scope: str) -> str:
    """Backwards-compatible alias for blind_index."""
    return _get_manager().fast_blind_index(data, scope)


def generate_key_pair() -> Tuple[bytes, bytes]:
    """
    Generate an Ed25519 key pair for digital signatures.

    Returns:
        Tuple[bytes, bytes]: (private_key, public_key)
    """
    return _generate_key_pair()


def sign_data(data: bytes, private_key: bytes) -> bytes:
    """
    Sign data using Ed25519.

    Args:
        data (bytes): Data to sign.
        private_key (bytes): Ed25519 private key.

    Returns:
        bytes: Signature.
    """
    return _sign_data(data, private_key)


def verify_signature(data: bytes, signature: bytes, public_key: bytes) -> bool:
    """
    Verify a signature using Ed25519.

    Args:
        data (bytes): Original data.
        signature (bytes): Signature to verify.
        public_key (bytes): Ed25519 public key.

    Returns:
        bool: True if signature is valid, False otherwise.
    """
    return _verify_signature(data, signature, public_key)


def generate_snowflake_id() -> int:
    """
    Generate a unique Snowflake ID.

    Returns:
        int: 64-bit unique identifier.
    """
    return _get_snowflake().generate()


def parse_snowflake_id(snowflake_id: int) -> dict:
    """
    Parse a Snowflake ID into its components.

    Args:
        snowflake_id (int): The Snowflake ID to parse.

    Returns:
        dict: Dictionary with timestamp, datacenter_id, worker_id, and sequence.
    """
    return _get_snowflake().parse(snowflake_id)


# === Message Encryption (at rest) ===


def encrypt_message(content: str, message_id: Optional[int] = None) -> str:
    """
    Encrypt message content for storage at rest.
    Uses AES-256-GCM with auto-generated key stored in ~/.plexichat/data/.message_encryption_key

    Args:
        content (str): The plaintext message content.
        message_id (int, optional): Message ID for additional integrity protection.

    Returns:
        str: Encrypted content with prefix marker (ENC:1:...).
    """
    return _get_message_encryptor().encrypt_message(content, message_id)


def decrypt_message(encrypted_content: str, message_id: Optional[int] = None) -> str:
    """
    Decrypt message content from storage.
    Automatically detects legacy plaintext messages and returns them unchanged.

    Args:
        encrypted_content (str): The encrypted content (or legacy plaintext).
        message_id (int, optional): Message ID used during encryption.

    Returns:
        str: Decrypted plaintext content.

    Raises:
        ValueError: If decryption fails (corrupted data or wrong key).
    """
    return _get_message_encryptor().decrypt_message(encrypted_content, message_id)


def is_message_encrypted(content: str) -> bool:
    """
    Check if message content is encrypted.

    Args:
        content (str): The message content to check.

    Returns:
        bool: True if content has encryption prefix, False otherwise.
    """
    return _get_message_encryptor().is_encrypted(content)


def is_message_key_auto_generated() -> bool:
    """
    Check if the message encryption key was auto-generated.
    Used for startup warnings to remind users to back up the key.

    Returns:
        bool: True if key was auto-generated, False if loaded from existing file.
    """
    return _get_message_encryptor().is_key_auto_generated()


def rotate_keys(force: bool = False) -> bool:
    """
    Rotate encryption keys if rotation period has passed.

    Args:
        force (bool): Force rotation regardless of time.

    Returns:
        bool: True if keys were rotated.
    """
    return _get_manager().rotate_keys(force)


# === File Encryption (at rest) ===


def encrypt_file(data: bytes, aad: Optional[bytes] = None) -> bytes:
    """
    Encrypt file data for storage at rest.

    Args:
        data (bytes): Raw file bytes.
        aad (bytes, optional): Additional Authenticated Data (e.g., file path).

    Returns:
        bytes: Encrypted blob with header.
    """
    from .file_encryption import encrypt_file as _encrypt_file

    return _encrypt_file(data, aad)


def decrypt_file(blob: bytes, aad: Optional[bytes] = None) -> bytes:
    """
    Decrypt file data from storage.

    Args:
        blob (bytes): Encrypted blob with header.
        aad (bytes, optional): Additional Authenticated Data used during encryption.

    Returns:
        bytes: Decrypted file data.
    """
    from .file_encryption import decrypt_file as _decrypt_file

    return _decrypt_file(blob, aad)


__all__ = [
    "EncryptionManager",
    "KeyringDecryptionError",
    "SnowflakeGenerator",
    "MessageEncryptor",
    "setup",
    "hash_password",
    "verify_password",
    "encrypt_data",
    "decrypt_data",
    "generate_key_pair",
    "sign_data",
    "verify_signature",
    "generate_snowflake_id",
    "parse_snowflake_id",
    "encrypt_message",
    "decrypt_message",
    "is_message_encrypted",
    "is_message_key_auto_generated",
    "rotate_keys",
    "encrypt_file",
    "decrypt_file",
]
