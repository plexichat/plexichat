"""
KEK (Key Encryption Key) Migration Tool

This tool provides secure migration of encryption keyrings when KEKs change.
It supports re-encrypting keyrings with new KEKs, with comprehensive logging,
rollback support, and secure cleanup of temporary files.

Usage:
    python -m src.utils.encryption.kek_migration --help
"""

from .migration import KeyringMigration, KEKMigrationError
from .utils import decode_env_key, get_keyring_paths
from .cli import (
    migrate_keyring,
    migrate_all_keyrings,
    rollback_keyring,
    validate_keyrings,
    main,
)

__all__ = [
    "KeyringMigration",
    "KEKMigrationError",
    "decode_env_key",
    "get_keyring_paths",
    "migrate_keyring",
    "migrate_all_keyrings",
    "rollback_keyring",
    "validate_keyrings",
    "main",
]

if __name__ == "__main__":
    main()
