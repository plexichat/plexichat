# Encryption Utility

A comprehensive encryption utility providing password hashing, data encryption, digital signatures, and unique ID generation using future-proof cryptographic standards.

## Features

- **Password Hashing**: Argon2id (OWASP recommended)
- **Data Encryption**: AES-256-GCM with authenticated encryption
- **Key Derivation**: PBKDF2-HMAC-SHA256 for key generation
- **Digital Signatures**: Ed25519 for fast, secure signatures
- **Unique IDs**: Twitter-style Snowflake IDs for distributed systems
- **Zero-friction Pattern**: Setup once,use anywhere

## Installation

Requires the following packages:

```bash
pip install argon2-cffi cryptography
```

## Usage

### Setup (Once in main.py)

In your main application file, setup the encryption module once:

```python
import utils.encryption as encryption

encryption.setup(
    worker_id=1,
    datacenter_id=1,
    argon2_time_cost=2,
    argon2_memory_cost=65536,
    argon2_parallelism=2
)
```

### Usage (In any other file)

In any other file in your project, just import and use:

```python
import utils.encryption as encryption

# Password hashing
password_hash = encryption.hash_password("my_secure_password")
is_valid = encryption.verify_password("my_secure_password", password_hash)

# Data encryption
encrypted = encryption.encrypt_data("sensitive data")
decrypted = encryption.decrypt_data(encrypted)

# With custom key
key = os.urandom(32)
encrypted = encryption.encrypt_data("secret", key)
decrypted = encryption.decrypt_data(encrypted, key)

# Digital signatures
private_key, public_key = encryption.generate_key_pair()
signature = encryption.sign_data(b"message", private_key)
is_valid = encryption.verify_signature(b"message", signature, public_key)

# Snowflake IDs
unique_id = encryption.generate_snowflake_id()
parsed = encryption.parse_snowflake_id(unique_id)
print(f"Timestamp: {parsed['timestamp']}")
```

### Setup is Optional

The encryption module auto-initializes with sensible defaults if setup is not called.

## Configuration Options

### setup() Parameters

| Parameter            | Description                                   | Default    |
| -------------------- | --------------------------------------------- | ---------- |
| `worker_id`          | Worker ID for Snowflake generation (0-31)     | 1          |
| `datacenter_id`      | Datacenter ID for Snowflake generation (0-31) | 1          |
| `epoch_timestamp`    | Custom epoch for Snowflake IDs (milliseconds) | 2024-01-01 |
| `argon2_time_cost`   | Argon2 time cost (iterations)                 | 2          |
| `argon2_memory_cost` | Argon2 memory cost (KiB)                      | 65536      |
| `argon2_parallelism` | Argon2 parallelism factor                     | 2          |

## API Reference

### Password Hashing

#### hash_password(password: str) -> str

Hash a password using Argon2id.

```python
hash_str = encryption.hash_password("my_password")
```

#### verify_password(password: str, hash_str: str) -> bool

Verify a password against its hash.

```python
is_valid = encryption.verify_password("my_password", hash_str)
```

### Data Encryption

#### encrypt_data(data: str, key: Optional[bytes] = None) -> str

Encrypt data using AES-256-GCM.

```python
encrypted = encryption.encrypt_data("sensitive data")
encrypted_with_key = encryption.encrypt_data("data", key)
```

#### decrypt_data(encrypted_data: str, key: Optional[bytes] = None) -> str

Decrypt data using AES-256-GCM.

```python
decrypted = encryption.decrypt_data(encrypted)
```

### Digital Signatures

#### generate_key_pair() -> Tuple[bytes, bytes]

Generate an Ed25519 key pair.

```python
private_key, public_key = encryption.generate_key_pair()
```

#### sign_data(data: bytes, private_key: bytes) -> bytes

Sign data using Ed25519.

```python
signature = encryption.sign_data(b"message", private_key)
```

#### verify_signature(data: bytes, signature: bytes, public_key: bytes) -> bool

Verify a signature.

```python
is_valid = encryption.verify_signature(b"message", signature, public_key)
```

### Snowflake IDs

#### generate_snowflake_id() -> int

Generate a unique 64-bit Snowflake ID.

```python
unique_id = encryption.generate_snowflake_id()
```

#### parse_snowflake_id(snowflake_id: int) -> dict

Parse a Snowflake ID into its components.

```python
parsed = encryption.parse_snowflake_id(unique_id)
# Returns: {
#     'timestamp': 1704067200000,
#     'datacenter_id': 1,
#     'worker_id': 1,
#     'sequence': 0
# }
```

## Security Considerations

1. **Password Hashing**: Uses Argon2id, winner of the Password Hashing Competition and recommended by OWASP. Each hash includes a unique salt.

2. **Data Encryption**: AES-256-GCM provides both confidentiality and authenticity. Each encryption uses a unique nonce.

3. **Digital Signatures**: Ed25519 provides ~128-bit security with small signatures (64 bytes) and fast verification.

4. **Key Storage**: Store encryption keys securely. Never commit keys to version control.
   - You can provide keys via environment variables for production deployments:
     - `PLEXICHAT_ENCRYPTION_KEY`: Base64 encoded 32-byte key for general data encryption.
     - `PLEXICHAT_MESSAGE_KEY`: Base64 encoded 32-byte key for message-at-rest encryption.
   - If no environment variable is found, the module will look for keys in `~/.plexichat/data/` or generate new ones if they don't exist.

5. **Custom Keys**: If using custom keys for encryption, ensure they are 32 bytes (256 bits) from a cryptographically secure random source.

6. **Snowflake IDs**: While unique, Snowflake IDs are not cryptographically secure random numbers. Do not use them for security tokens.

## Snowflake ID Structure

Snowflake IDs are 64-bit integers composed of:

- **41 bits**: Timestamp in milliseconds since epoch (69 years)
- **5 bits**: Datacenter ID (32 datacenters)
- **5 bits**: Worker ID (32 workers per datacenter)
- **12 bits**: Sequence number (4096 IDs per millisecond)

This allows for:

- 1024 unique worker/datacenter combinations
- 4,096,000 unique IDs per second per worker
- Time-ordered IDs
- Distributed generation without coordination

## Error Handling

The module raises appropriate exceptions:

- `ValueError`: Invalid input (empty password, wrong key length, malformed data)
- `RuntimeError`: System errors (clock moved backwards for Snowflake generation)

All cryptographic operations that may fail (verification, decryption) return `False` or raise `ValueError` rather than exposing cryptographic details.

## Performance

- **Argon2id**: Configurable time/memory trade-off. Defaults balance security and performance.
- **AES-256-GCM**: Hardware-accelerated on most modern CPUs.
- **Ed25519**: ~10,000 signatures/sec and ~20,000 verifications/sec on typical hardware.
- **Snowflake IDs**: Millions of IDs per second with thread-safe generation.

## Thread Safety

- Password hashing: Thread-safe
- Data encryption: Thread-safe (each encryption uses unique nonce)
- Snowflake generation: Thread-safe (uses internal locking)
- Key pair generation: Thread-safe

## Examples

### User Authentication Flow

```python
import utils.encryption as encryption

# Registration
password = "user_password"  # pragma: allowlist secret
password_hash = encryption.hash_password(password)
# Store password_hash in database

# Login
entered_password = "user_password"
if encryption.verify_password(entered_password, password_hash):
    print("Login successful")
else:
    print("Invalid password")
```

### Secure Message Storage

```python
import utils.encryption as encryption

# Encryption
message = "Confidential message"
encrypted_message = encryption.encrypt_data(message)
# Store encrypted_message in database

# Decryption
retrieved_message = encryption.decrypt_data(encrypted_message)
print(retrieved_message)  # "Confidential message"
```

### Message Signing

```python
import utils.encryption as encryption

# Generate keys (do once)
private_key, public_key = encryption.generate_key_pair()

# Sign message
message = b"Important message"
signature = encryption.sign_data(message, private_key)

# Verify signature
is_authentic = encryption.verify_signature(message, signature, public_key)
print(f"Message is authentic: {is_authentic}")
```

### Distributed ID Generation

```python
import utils.encryption as encryption

# Setup for worker 1 in datacenter 1
encryption.setup(worker_id=1, datacenter_id=1)

# Generate IDs
user_id = encryption.generate_snowflake_id()
post_id = encryption.generate_snowflake_id()
message_id = encryption.generate_snowflake_id()

# Parse ID to get timestamp
parsed = encryption.parse_snowflake_id(user_id)
print(f"User created at: {parsed['timestamp']}")
```

## Testing

Run the comprehensive test suite:

```bash
pytest src/tests/encryption/test_encryption.py -v
```

The test suite includes 53 tests covering all features and edge cases.

## Channel Ratchet (v3)

The encryption module also ships a per-channel key ratchet for
at-rest message encryption. See
[`channel_ratchet/README.md`](channel_ratchet/README.md) for the
threat model, wire format, rotation rules, and split-on-delete
semantics. The ratchet is gated by the license feature
`channel_ratchet_encryption` and exposes a small public surface:

* `ChannelRatchetManager.encrypt(conversation_id, message_id, plaintext)`
* `ChannelRatchetManager.decrypt(conversation_id, message_id, envelope)`
* `ChannelRatchetManager.snapshot(conversation_id)`

The wire format is `ENC:3:{interval_id}:{base64(nonce||ct||tag)}`,
distinct from the legacy per-keyring `ENC:1:...` and `ENC:2:...`
envelopes, so the decrypt path can dispatch by prefix.
