# Unit tests

This directory contains isolated unit tests for core utilities. These tests
run without external dependencies and use real implementations (no mocks for
hashing or validation logic).

## Test files

| File | Description |
|------|-------------|
| `test_real_hashing.py` | Argon2id password hashing and verification |
| `test_validators.py` | Username, email, password, server name, and message validators |

Additional unit test files cover configuration, networking, licensing, TLS,
telemetry, admin security, and property-based edge cases.

## What these tests cover

### Password hashing (`test_real_hashing.py`)

- Hashing returns a string in Argon2id PHC format (`$argon2id$...`)
- Same password produces different hashes due to random salt
- Verification succeeds for correct passwords and fails for wrong or empty
  passwords
- Hashing an empty password raises `ValueError`
- Argon2 parameters (memory cost, time cost, parallelism) are present and
  secure
- Hashing takes between 10ms and 2s -- fast enough for UX but slow enough
  to resist brute-force attacks
- Various password formats (simple, uppercase, special characters, unicode,
  long strings) hash and verify correctly
- Invalid hash formats are rejected gracefully

### Input validators (`test_validators.py`)

- **Username** -- property-based tests with Hypothesis verify that the real
  `validate_username` logic enforces length (3--32 chars), printable characters,
  and rejects reserved names like `admin` and `root`
- **Email** -- property-based tests verify `validate_email` returns a boolean
  for valid and invalid patterns
- **Password** -- property-based tests verify `validate_password` returns a
  result object with `valid`, `score`, and `issues` attributes
- **Server name** -- property-based tests verify `_validate_server_name`
  enforces length (2--100 chars) after stripping
- **Message content** -- property-based tests verify `validate_content`
  enforces a 4000-character limit and sanitizes dangerous patterns

## Running the tests

```bash
# Run all unit tests
pytest src/tests/unit/ -v

# Run hashing tests only
pytest src/tests/unit/test_real_hashing.py -v

# Run validator tests only (requires hypothesis)
pytest src/tests/unit/test_validators.py -v

# Run with coverage
pytest src/tests/unit/ --cov=src.utils.encryption --cov=src.core.auth -v
```

## Key classes and fixtures

- `TestPasswordHashing` -- hash generation, verification, and edge cases
- `TestArgon2Parameters` -- parameter validation and timing checks
- `TestUsernameValidation` -- property-based and parameterized username tests
- `TestEmailValidation` -- property-based and parameterized email tests
- `TestPasswordValidation` -- property-based password strength tests
- `TestServerNameValidation` -- property-based server name tests
- `TestMessageLimits` -- property-based message content tests

## Dependencies

- `hypothesis` -- required for property-based tests in `test_validators.py`;
  tests are skipped if the package is not installed
- `src.utils.encryption.core.EncryptionManager` -- real Argon2id hasher used in
  hashing tests (no mocks)
- `src.core.auth.passwords` -- real validation functions used in validator tests

## Related source code

- `src/utils/encryption/core.py` -- `EncryptionManager` with Argon2id hashing
- `src/core/auth/passwords.py` -- `validate_username`, `validate_email`, `validate_password`
- `src/core/messaging/content.py` -- `validate_content`
- `src/core/servers/manager.py` -- `ServerManager._validate_server_name`
