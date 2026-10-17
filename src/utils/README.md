# Utilities

Shared utility modules for the Plexichat server and client.

## Modules

| Module | Description |
| --- | --- |
| `common_utils/` | Shared utilities: config loader, logger, validator, and version parser |
| `encryption/` | AES-256-GCM encryption, password hashing (Argon2id), digital signatures (Ed25519), and channel ratchet protocol |

## Usage

Import utilities from the `src.utils` package:

```python
from src.utils import config, logger, validator, version
from src.utils.encryption import encrypt, decrypt, hash_password
```

## Architecture

The `common_utils` package provides the canonical implementations under
`src/utils/common_utils/utils/`. The top-level modules (`config.py`,
`logger.py`, etc.) re-export these implementations so that both import
paths resolve to the same module objects.

The `encryption` module is self-contained and provides a singleton
`EncryptionManager`. Call `encryption.setup()` once at application start
to configure worker IDs and Argon2 parameters, or rely on defaults.
