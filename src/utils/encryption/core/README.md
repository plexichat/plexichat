# Encryption Core Sub-Package

## Purpose
Provides low-level encryption primitives: password hashing (Argon2id), data encryption (AES-256-GCM), blind indexing, key rotation, Snowflake ID generation, message encryptors, and digital signatures.

## Architecture
The `EncryptionManager` is composed via mixin pattern (MRO). Each file handles a specific domain:

| File | Component | Responsibilities |
|------|-----------|-----------------|
| `protocol.py` | `EncryptionCoreProtocol` | Shared attribute type annotations for mixins |
| `file_lock.py` | — | Cross-platform file locking helpers |
| `keyring.py` | `Keyring`, `KeyringDecryptionError` | Encrypted key storage, KEK management, key rotation |
| `password.py` | `PasswordMixin` | `hash_password`, `verify_password`, `derive_key` |
| `crypto.py` | `CryptoMixin` | `encrypt_data`, `decrypt_data` with AAD support |
| `blind_index.py` | `BlindIndexMixin` | `blind_index`, `fast_blind_index`, `legacy_fast_blind_index` |
| `rotation.py` | `RotationMixin` | `rotate_keys` automated key rotation |
| `snowflake.py` | `SnowflakeGenerator` | Twitter-style snowflake ID generation |
| `message_encryptor.py` | `MessageEncryptor` | Per-message AES-256-GCM encryption |
| `signing.py` | — | Ed25519 `generate_key_pair`, `sign_data`, `verify_signature` |
| `manager.py` | `EncryptionManager` | Composes all mixins |

## Usage

```python
from src.utils.encryption.core import EncryptionManager, Keyring, SnowflakeGenerator

manager = EncryptionManager()
hashed = manager.hash_password("my_password")
encrypted = manager.encrypt_data("sensitive data")
decrypted = manager.decrypt_data(encrypted)
```

## Protocol Pattern
Mixins use `EncryptionCoreProtocol` as the base class, which declares shared state types (`password_hasher`, `keyring`) so pyright understands the full type context.
