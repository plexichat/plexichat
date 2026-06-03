# Encryption Core Sub-Package

## Purpose
Provides low-level encryption primitives: password hashing (Argon2id), data encryption (AES-256-GCM), blind indexing, key rotation, Snowflake ID generation, message encryptors, and digital signatures. The `Keyring` class also exposes `wrap`/`unwrap` helpers used by the channel ratchet to store `start_key` material at rest.

## Architecture
The `EncryptionManager` is composed via mixin pattern (MRO). Each file handles a specific domain:

| File | Component | Responsibilities |
|------|-----------|-----------------|
| `protocol.py` | `EncryptionCoreProtocol` | Shared attribute type annotations for mixins |
| `file_lock.py` | — | Cross-platform file locking helpers |
| `keyring.py` | `Keyring`, `KeyringDecryptionError` | Encrypted key storage, KEK management, key rotation, `wrap`/`unwrap` helpers |
| `password.py` | `PasswordMixin` | `hash_password`, `verify_password`, `derive_key` |
| `crypto.py` | `CryptoMixin` | `encrypt_data`, `decrypt_data` with AAD support |
| `blind_index.py` | `BlindIndexMixin` | `blind_index`, `fast_blind_index`, `legacy_fast_blind_index` |
| `rotation.py` | `RotationMixin` | `rotate_keys` automated key rotation |
| `snowflake.py` | `SnowflakeGenerator` | Twitter-style snowflake ID generation |
| `message_encryptor.py` | `MessageEncryptor` | Per-message AES-256-GCM encryption (legacy v1/v2 envelopes) |
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

## Keyring.wrap / Keyring.unwrap

The `Keyring` class exposes two small helpers that wrap an
arbitrary byte blob with the current key (and unwrap on the way
back). The channel ratchet uses these to store its `start_key`
material at rest in the same `ENC:{version}:{base64(...)}` format
the keyring itself uses:

```python
from src.utils.encryption.core import Keyring

keyring = Keyring("/path/to/message_keyring.json")
wrapped = keyring.wrap(b"\x00" * 32)        # ENC:1:AbCdEf==...
raw = keyring.unwrap(wrapped)               # b"\x00" * 32
```
