# KEK Migration

Migration tool for re-encrypting encryption keyrings when the Key Encryption Key (KEK) changes. Provides secure re-encryption with backup, rollback, validation, and comprehensive logging.

## Overview

Plexichat encrypts data using a two-tier key hierarchy: a KEK wraps data encryption keys stored in keyrings. When the KEK rotates (e.g., during key compromise or scheduled rotation), all keyrings must be re-encrypted with the new KEK. This module handles that migration safely.

Key capabilities:

- **Single keyring migration**: Decrypt with old KEK, re-encrypt with new KEK.
- **Bulk migration**: Migrate all known keyrings (system, message, file) in one pass.
- **Validation**: Verify that a keyring can be decrypted with a given KEK before migration.
- **Backup and rollback**: Automatic backup before migration; restore on failure or on demand.
- **Dry-run mode**: Validate without making changes.
- **CLI interface**: `python -m src.utils.encryption.kek_migration` for operational use.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  CLI (cli.py)                                        │
│  ┌──────────┬───────────┬──────────┬──────────────┐  │
│  │ migrate  │ migrate   │ rollback │  validate    │  │
│  │ _keyring │ _all      │ _keyring │  _keyrings   │  │
│  └────┬─────┴─────┬─────┴────┬─────┴──────┬───────┘  │
│       │           │          │             │          │
│  ┌────┴───────────┴──────────┴─────────────┴───────┐ │
│  │  KeyringMigration (migration.py)                 │ │
│  │  ┌───────────┬──────────┬──────────┬──────────┐  │ │
│  │  │ validate  │ backup   │ migrate  │ rollback │  │ │
│  │  │ _keyring  │ _keyring │ _kek     │          │  │ │
│  │  └───────────┴──────────┴──────────┴──────────┘  │ │
│  └──────────────────────────────────────────────────┘ │
│                       │                               │
│  ┌────────────────────┴──────────────────────────┐   │
│  │  utils.py — decode_env_key, get_keyring_paths │   │
│  └───────────────────────────────────────────────┘   │
│                       │                               │
│  ┌────────────────────┴──────────────────────────┐   │
│  │  ~/.plexichat/data/                            │   │
│  │  ├── system_keyring.json                       │   │
│  │  ├── message_keyring.json                      │   │
│  │  └── file_keyring.json                         │   │
│  └───────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────┘
```

## Key files

| File | Purpose |
|---|---|
| `__init__.py` | Module facade — re-exports all public names |
| `migration.py` | `KeyringMigration` class — validate, backup, migrate, rollback |
| `protocol.py` | `KEKMigrationProtocol` — method signature protocol for mixins |
| `cli.py` | CLI entry point — `migrate_keyring()`, `migrate_all_keyrings()`, `rollback_keyring()`, `validate_keyrings()`, `main()` |
| `utils.py` | `decode_env_key()` hex/base64 decoding, `get_keyring_paths()` |

## Usage

### CLI usage

```bash
# Migrate a specific keyring
python -m src.utils.encryption.kek_migration \
  --keyring message_keyring.json \
  --old-kek-env PLEXICHAT_SYSTEM_KEY \
  --new-kek-env PLEXICHAT_MESSAGE_KEY

# Migrate all keyrings
python -m src.utils.encryption.kek_migration \
  --all \
  --new-kek-env PLEXICHAT_NEW_KEK

# Validate keyrings without migrating
python -m src.utils.encryption.kek_migration --validate
python -m src.utils.encryption.kek_migration --validate --all

# Rollback a migration
python -m src.utils.encryption.kek_migration \
  --rollback \
  --keyring message_keyring.json

# Dry run (validate only, no changes)
python -m src.utils.encryption.kek_migration \
  --keyring message_keyring.json \
  --old-kek-env PLEXICHAT_SYSTEM_KEY \
  --new-kek-env PLEXICHAT_MESSAGE_KEY \
  --dry-run
```

### Programmatic usage

```python
from src.utils.encryption.kek_migration import (
    KeyringMigration,
    decode_env_key,
    get_keyring_paths,
)
from pathlib import Path

# Decode KEKs from environment variables
old_kek = decode_env_key(os.environ["PLEXICHAT_SYSTEM_KEY"])
new_kek = decode_env_key(os.environ["PLEXICHAT_NEW_KEK"])

# Migrate a single keyring
keyring_path = Path.home() / ".plexichat" / "data" / "system_keyring.json"
migration = KeyringMigration(keyring_path)

# Validate first
result = migration.validate_keyring(old_kek, new_kek)
if result["can_decrypt_with_old_kek"]:
    migration.migrate_kek(old_kek, new_kek)

# Rollback if needed
migration.rollback()
```

## Keyring format

Keyrings are AES-256-GCM encrypted JSON files:

```json
{
    "nonce": "base64_encoded_12_byte_nonce",
    "payload": "base64_encoded_aes_gcm_ciphertext"
}
```

The decrypted payload contains:

```json
{
    "current_version": 3,
    "rotated_at": 1746292800,
    "keys": {
        "1": "base64_encoded_key",
        "2": "base64_encoded_key",
        "3": "base64_encoded_key"
    }
}
```

## Keyring files

| File | Environment variable | Purpose |
|---|---|---|
| `system_keyring.json` | `PLEXICHAT_SYSTEM_KEY` | System-wide encryption keys |
| `message_keyring.json` | `PLEXICHAT_MESSAGE_KEY` | Message encryption keys |
| `file_keyring.json` | `PLEXICHAT_MEDIA_KEY` | File/media encryption keys |

## Migration workflow

1. **Validate**: Verify the keyring can be decrypted with the old KEK.
2. **Backup**: Copy the keyring to a timestamped backup file.
3. **Decrypt**: Read and decrypt with the old KEK.
4. **Re-encrypt**: Encrypt the plaintext with the new KEK (new random nonce).
5. **Atomic swap**: Write to temp file, then `os.replace()` to the original path.
6. **Post-validate**: Verify the migrated keyring decrypts with the new KEK.
7. **Cleanup**: Remove backup and metadata files on success.

On failure at any step, the backup is restored automatically.

## Environment variables

| Variable | Purpose |
|---|---|
| `PLEXICHAT_SYSTEM_KEY` | Old or new KEK for the system keyring (hex or base64) |
| `PLEXICHAT_MESSAGE_KEY` | Old or new KEK for the message keyring |
| `PLEXICHAT_MEDIA_KEY` | Old or new KEK for the file/media keyring |

Keys must be 32 bytes, encoded as 64-character hex or base64.

## Related code

- `src/utils/encryption/kek_migration/__init__.py:1` — Module entry point and re-exports
- `src/utils/encryption/kek_migration/migration.py:32` — `KeyringMigration` class
- `src/utils/encryption/kek_migration/migration.py:181` — `migrate_kek()` core migration logic
- `src/utils/encryption/kek_migration/migration.py:294` — `rollback()` restore from backup
- `src/utils/encryption/kek_migration/cli.py:292` — `main()` CLI entry point
- `src/utils/encryption/kek_migration/utils.py:8` — `decode_env_key()` hex/base64 decoding
- `src/utils/encryption/kek_migration/protocol.py:11` — `KEKMigrationProtocol`
