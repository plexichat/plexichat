# Encryption Tests

Comprehensive test suite for encryption utilities covering cryptographic operations, security compliance, and edge cases.

## Test Files

### `test_encryption.py`
Core encryption functionality tests including:
- **Password Hashing**: Argon2id hashing and verification with timing attack resistance
- **Data Encryption**: AES-256-GCM encryption/decryption operations
- **Digital Signatures**: Ed25519 key generation, signing, and verification
- **Snowflake IDs**: Distributed unique ID generation and parsing
- **Message Encryption**: At-rest message encryption with AAD
- **Secure Tokens**: Token generation and validation with constant-time comparison
- **Edge Cases**: Boundary conditions, malformed data, concurrent operations

### `test_key_rotation.py`
Advanced key rotation tests including:
- **Rotation Lifecycle**: Key versioning and rotation mechanics
- **Migration Scenarios**: Legacy key file migration to keyring
- **Concurrent Access**: Thread-safe rotation and concurrent read/write operations
- **Data Integrity**: Decryption of data encrypted with older key versions
- **Error Handling**: Invalid version requests and corruption recovery
- **Automatic Rotation**: Time-based and forced rotation triggers

### `test_algorithm_compliance.py`
Cryptographic algorithm compliance tests including:
- **Argon2id Compliance**: OWASP recommendations for password hashing
- **AES-256-GCM Compliance**: NIST standards for authenticated encryption
- **Ed25519 Compliance**: RFC 8032 digital signature specification
- **PBKDF2 Compliance**: NIST SP 800-132 key derivation standards
- **Randomness Testing**: Entropy verification for cryptographic operations
- **Encoding Standards**: Base64 and hex encoding verification
- **Security Best Practices**: Timing-safe comparisons and information leakage prevention

## Test Coverage

The test suite covers:
- ✓ Password hashing with Argon2id (salt uniqueness, parameter verification)
- ✓ Timing attack resistance in password and token verification
- ✓ AES-256-GCM encryption with nonce uniqueness and authentication
- ✓ Ed25519 digital signatures (deterministic, unique per message/key)
- ✓ Key rotation with backward compatibility
- ✓ Message encryption with additional authenticated data (AAD)
- ✓ Secure token generation (high entropy, URL-safe encoding)
- ✓ Cryptographic edge cases (empty data, binary data, concurrent operations)
- ✓ Algorithm compliance with industry standards (NIST, OWASP, RFC 8032)

## Running Tests

```bash
# Run all encryption tests
pytest src/tests/encryption/

# Run specific test file
pytest src/tests/encryption/test_encryption.py
pytest src/tests/encryption/test_key_rotation.py
pytest src/tests/encryption/test_algorithm_compliance.py

# Run specific test class
pytest src/tests/encryption/test_encryption.py::TestPasswordHashing
pytest src/tests/encryption/test_key_rotation.py::TestKeyRotationLifecycle
pytest src/tests/encryption/test_algorithm_compliance.py::TestAES256GCMCompliance

# Run with verbose output
pytest src/tests/encryption/ -v

# Run with coverage
pytest src/tests/encryption/ --cov=src.utils.encryption --cov-report=html
```

## Test Dependencies

Tests require:
- pytest
- argon2-cffi (Argon2id password hashing)
- cryptography (AES-GCM, Ed25519, PBKDF2)
- Standard library: os, base64, hashlib, threading, time
