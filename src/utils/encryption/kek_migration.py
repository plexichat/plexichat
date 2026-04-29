"""
KEK (Key Encryption Key) Migration Tool

This tool provides secure migration of encryption keyrings when KEKs change.
It supports re-encrypting keyrings with new KEKs, with comprehensive logging,
rollback support, and secure cleanup of temporary files.

Usage:
    python -m src.utils.encryption.kek_migration --help

Examples:
    # Migrate a specific keyring with new KEK from environment variable
    python -m src.utils.encryption.kek_migration --keyring message_keyring.json --new-kek-env PLEXICHAT_MESSAGE_KEY

    # Migrate all keyrings to new KEKs
    python -m src.utils.encryption.kek_migration --all --new-kek-env PLEXICHAT_SYSTEM_KEY

    # Rollback a migration
    python -m src.utils.encryption.kek_migration --rollback --keyring message_keyring.json

    # Validate keyrings without migration
    python -m src.utils.encryption.kek_migration --validate --all
"""

import os
import sys
import json
import shutil
import base64
import hashlib
import argparse
import logging
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any
from datetime import datetime
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


class KEKMigrationError(Exception):
    """Raised when KEK migration fails."""

    pass


class KeyringMigration:
    """
    Handles migration of keyrings between different KEKs.

    Provides secure re-encryption with rollback support, comprehensive logging,
    and cleanup of temporary files.
    """

    def __init__(self, keyring_path: Path, dry_run: bool = False):
        self.keyring_path = keyring_path
        self.dry_run = dry_run
        self.backup_path = (
            keyring_path.parent
            / f"{keyring_path.stem}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}{keyring_path.suffix}"
        )
        self.temp_path = (
            keyring_path.parent / f"{keyring_path.stem}_temp{keyring_path.suffix}"
        )
        self.rollback_path = (
            keyring_path.parent / f"{keyring_path.stem}_rollback{keyring_path.suffix}"
        )
        self.metadata_path = (
            keyring_path.parent / f"{keyring_path.stem}_migration_metadata.json"
        )

    def validate_keyring(
        self, old_kek: bytes, new_kek: Optional[bytes] = None
    ) -> Dict[str, Any]:
        """
        Validate that a keyring can be decrypted with the given KEK.

        Args:
            old_kek: The current KEK to validate against
            new_kek: Optional new KEK to validate for future use

        Returns:
            Dictionary with validation results
        """
        logger.info(f"Validating keyring: {self.keyring_path.name}")

        result = {
            "keyring_exists": self.keyring_path.exists(),
            "can_decrypt_with_old_kek": False,
            "can_decrypt_with_new_kek": False,
            "key_count": 0,
            "current_version": 0,
            "rotated_at": 0,
            "validation_errors": [],
        }

        if not result["keyring_exists"]:
            result["validation_errors"].append("Keyring file does not exist")
            return result

        try:
            with open(self.keyring_path, "r") as f:
                encrypted_data = json.load(f)

            # Try decryption with old KEK
            try:
                aesgcm = AESGCM(old_kek)
                payload = base64.b64decode(encrypted_data["payload"])
                nonce = base64.b64decode(encrypted_data["nonce"])
                decrypted = aesgcm.decrypt(nonce, payload, None)
                data = json.loads(decrypted)

                result["can_decrypt_with_old_kek"] = True
                result["key_count"] = len(data.get("keys", {}))
                result["current_version"] = data.get("current_version", 0)
                result["rotated_at"] = data.get("rotated_at", 0)

                logger.info(
                    f"Successfully decrypted with old KEK: {result['key_count']} key version(s)"
                )

            except Exception as e:
                result["validation_errors"].append(
                    f"Failed to decrypt with old KEK: {e}"
                )
                logger.error(f"Old KEK decryption failed: {e}")

            # Try decryption with new KEK if provided
            if new_kek:
                try:
                    aesgcm = AESGCM(new_kek)
                    payload = base64.b64decode(encrypted_data["payload"])
                    nonce = base64.b64decode(encrypted_data["nonce"])
                    decrypted = aesgcm.decrypt(nonce, payload, None)
                    data = json.loads(decrypted)

                    result["can_decrypt_with_new_kek"] = True
                    logger.info(
                        f"Successfully decrypted with new KEK: {result['key_count']} key version(s)"
                    )

                except Exception as e:
                    result["validation_errors"].append(
                        f"Failed to decrypt with new KEK: {e}"
                    )
                    logger.error(f"New KEK decryption failed: {e}")

        except Exception as e:
            result["validation_errors"].append(f"Failed to read keyring file: {e}")
            logger.error(f"Keyring file read failed: {e}")

        return result

    def backup_keyring(self) -> bool:
        """
        Create a backup of the keyring file.

        Returns:
            True if backup successful, False otherwise
        """
        logger.info(f"Creating backup: {self.backup_path.name}")

        if not self.keyring_path.exists():
            logger.error("Cannot backup non-existent keyring")
            return False

        try:
            shutil.copy2(self.keyring_path, self.backup_path)
            logger.info(f"Backup created successfully: {self.backup_path}")
            return True
        except Exception as e:
            logger.error(f"Backup creation failed: {e}")
            return False

    def restore_backup(self) -> bool:
        """
        Restore keyring from backup.

        Returns:
            True if restore successful, False otherwise
        """
        logger.info(f"Restoring from backup: {self.backup_path.name}")

        if not self.backup_path.exists():
            logger.error("Backup file does not exist")
            return False

        try:
            shutil.copy2(self.backup_path, self.keyring_path)
            logger.info(f"Backup restored successfully: {self.keyring_path.name}")
            return True
        except Exception as e:
            logger.error(f"Backup restore failed: {e}")
            return False

    def migrate_kek(self, old_kek: bytes, new_kek: bytes, force: bool = False) -> bool:
        """
        Migrate keyring from old KEK to new KEK.

        Args:
            old_kek: The current KEK used to encrypt the keyring
            new_kek: The new KEK to encrypt the keyring with
            force: Force migration even if validation fails

        Returns:
            True if migration successful, False otherwise
        """
        logger.info(f"Starting KEK migration for: {self.keyring_path.name}")
        logger.info(f"Old KEK source: {self._kek_source_description(old_kek)}")
        logger.info(f"New KEK source: {self._kek_source_description(new_kek)}")

        # Validate current state
        validation = self.validate_keyring(old_kek, new_kek)

        if not validation["can_decrypt_with_old_kek"]:
            logger.error("Cannot decrypt keyring with old KEK - aborting migration")
            return False

        if validation["can_decrypt_with_new_kek"] and not force:
            logger.warning(
                "Keyring can already be decrypted with new KEK - use --force to re-encrypt anyway"
            )
            return False

        if self.dry_run:
            logger.info("Dry run mode - would migrate keyring successfully")
            return True

        # Create backup
        if not self.backup_keyring():
            logger.error("Backup failed - aborting migration for safety")
            return False

        try:
            # Read and decrypt with old KEK
            with open(self.keyring_path, "r") as f:
                encrypted_data = json.load(f)

            aesgcm_old = AESGCM(old_kek)
            payload = base64.b64decode(encrypted_data["payload"])
            nonce = base64.b64decode(encrypted_data["nonce"])
            decrypted = aesgcm_old.decrypt(nonce, payload, None)
            keyring_data = json.loads(decrypted)

            key_count = len(keyring_data.get("keys", {}))
            logger.info(f"Decrypted keyring with {key_count} key version(s)")

            # Save metadata for rollback
            self._save_migration_metadata(keyring_data, old_kek, new_kek)

            # Re-encrypt with new KEK
            aesgcm_new = AESGCM(new_kek)
            new_nonce = os.urandom(12)
            new_payload = aesgcm_new.encrypt(
                new_nonce, json.dumps(keyring_data).encode(), None
            )

            new_encrypted_data = {
                "nonce": base64.b64encode(new_nonce).decode(),
                "payload": base64.b64encode(new_payload).decode(),
            }

            # Write to temporary file first
            with open(self.temp_path, "w") as f:
                json.dump(new_encrypted_data, f)

            # Set permissions
            try:
                os.chmod(self.temp_path, 0o600)
            except (OSError, AttributeError):
                pass

            # Atomic swap
            os.replace(self.temp_path, self.keyring_path)

            logger.info(
                f"Successfully migrated keyring to new KEK: {self.keyring_path.name}"
            )

            # Validate the migration
            post_validation = self.validate_keyring(new_kek, old_kek)
            if not post_validation["can_decrypt_with_new_kek"]:
                logger.error("Post-migration validation failed - rolling back")
                self.rollback()
                return False

            # Clean up backup on success
            try:
                os.remove(self.backup_path)
                logger.info(f"Cleaned up backup: {self.backup_path.name}")
            except Exception as e:
                logger.warning(f"Failed to remove backup file: {e}")

            # Clean up metadata
            try:
                os.remove(self.metadata_path)
                logger.info(f"Cleaned up migration metadata: {self.metadata_path.name}")
            except Exception as e:
                logger.warning(f"Failed to remove metadata file: {e}")

            return True

        except Exception as e:
            logger.error(f"Migration failed: {e}")
            logger.info("Attempting rollback...")
            self.rollback()
            return False

    def rollback(self) -> bool:
        """
        Rollback to the backup keyring.

        Returns:
            True if rollback successful, False otherwise
        """
        logger.info("Rolling back migration...")

        if not self.backup_path.exists():
            logger.error("No backup file available for rollback")
            return False

        try:
            # Restore from backup
            shutil.copy2(self.backup_path, self.keyring_path)
            logger.info(f"Rollback successful: {self.keyring_path.name}")

            # Clean up temporary files
            for temp_file in [self.temp_path, self.rollback_path, self.metadata_path]:
                try:
                    if temp_file.exists():
                        os.remove(temp_file)
                        logger.info(f"Cleaned up temporary file: {temp_file.name}")
                except Exception as e:
                    logger.warning(f"Failed to remove temporary file: {e}")

            return True

        except Exception as e:
            logger.error(f"Rollback failed: {e}")
            return False

    def _save_migration_metadata(
        self, keyring_data: Dict[str, Any], old_kek: bytes, new_kek: bytes
    ) -> None:
        """Save migration metadata for rollback and audit purposes."""
        metadata = {
            "timestamp": datetime.now().isoformat(),
            "keyring_path": str(self.keyring_path),
            "backup_path": str(self.backup_path),
            "old_kek_hash": hashlib.sha256(old_kek).hexdigest(),
            "new_kek_hash": hashlib.sha256(new_kek).hexdigest(),
            "keyring_data": {
                "current_version": keyring_data.get("current_version"),
                "key_count": len(keyring_data.get("keys", {})),
                "rotated_at": keyring_data.get("rotated_at"),
            },
        }

        with open(self.metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)

        logger.info(f"Migration metadata saved: {self.metadata_path.name}")

    def _kek_source_description(self, kek: bytes) -> str:
        """Get a human-readable description of the KEK source."""
        # Check if it matches an environment variable
        env_vars = [
            "PLEXICHAT_SYSTEM_KEY",
            "PLEXICHAT_MESSAGE_KEY",
            "PLEXICHAT_MEDIA_KEY",
        ]
        for env_var in env_vars:
            env_value = os.environ.get(env_var)
            if env_value:
                try:
                    # Try Base64
                    decoded = base64.b64decode(env_value)
                    if decoded == kek:
                        return f"Environment variable {env_var} (Base64)"
                except:
                    pass
                try:
                    # Try hex
                    decoded = bytes.fromhex(env_value)
                    if decoded == kek:
                        return f"Environment variable {env_var} (hex)"
                except:
                    pass

        # Check if it matches machine key file
        try:
            machine_key_path = Path.home() / ".plexichat" / "data" / ".machine_key"
            if machine_key_path.exists():
                if machine_key_path.read_bytes() == kek:
                    return "Machine-local key file"
        except:
            pass

        return "Unknown source"

    def cleanup_temp_files(self) -> None:
        """Clean up all temporary files created during migration."""
        logger.info("Cleaning up temporary files...")

        temp_files = [self.temp_path, self.rollback_path, self.metadata_path]

        for temp_file in temp_files:
            try:
                if temp_file.exists():
                    os.remove(temp_file)
                    logger.info(f"Removed temporary file: {temp_file.name}")
            except Exception as e:
                logger.warning(f"Failed to remove temporary file {temp_file}: {e}")


def decode_env_key(env_value: str) -> bytes:
    """
    Decode environment variable key (supports hex and Base64).

    Tries hex first (the standard Plexichat production format: 64-char hex = 32 bytes),
    then falls back to Base64. This order is important because a 64-char hex string
    decodes to 48 bytes in Base64 — it passes the length check incorrectly when
    Base64 is tried first.

    Args:
        env_value: The environment variable value

    Returns:
        The decoded 32-byte key

    Raises:
        ValueError: If the key cannot be decoded or is not 32 bytes
    """
    # Try hex first (standard Plexichat production format: 64 hex chars = 32 bytes)
    try:
        key = bytes.fromhex(env_value)
        if len(key) == 32:
            return key
    except Exception:
        pass

    # Try Base64 (alternative format)
    try:
        key = base64.b64decode(env_value)
        if len(key) == 32:
            return key
    except Exception:
        pass

    raise ValueError(
        f"Environment variable must be a 32-byte key (hex or Base64 encoded), got {len(env_value)} characters"
    )


def get_keyring_paths() -> List[Path]:
    """Get all keyring file paths."""
    data_dir = Path.home() / ".plexichat" / "data"
    keyring_files = [
        data_dir / "system_keyring.json",
        data_dir / "message_keyring.json",
        data_dir / "file_keyring.json",
    ]

    return [f for f in keyring_files if f.exists()]


def migrate_keyring(
    keyring_name: str,
    old_kek_env: str,
    new_kek_env: str,
    force: bool = False,
    dry_run: bool = False,
) -> bool:
    """
    Migrate a specific keyring from old KEK to new KEK.

    Args:
        keyring_name: Name of the keyring file (e.g., "message_keyring.json")
        old_kek_env: Environment variable name for the old KEK
        new_kek_env: Environment variable name for the new KEK
        force: Force migration even if validation fails
        dry_run: Validate only without making changes

    Returns:
        True if migration successful, False otherwise
    """
    logger.info(f"=== KEK Migration Started ===")
    logger.info(f"Keyring: {keyring_name}")
    logger.info(f"Old KEK env var: {old_kek_env}")
    logger.info(f"New KEK env var: {new_kek_env}")
    logger.info(f"Force: {force}")
    logger.info(f"Dry run: {dry_run}")

    # Get KEK values from environment
    old_kek_value = os.environ.get(old_kek_env)
    new_kek_value = os.environ.get(new_kek_env)

    if not old_kek_value:
        logger.error(f"Old KEK environment variable not set: {old_kek_env}")
        return False

    if not new_kek_value:
        logger.error(f"New KEK environment variable not set: {new_kek_env}")
        return False

    try:
        old_kek = decode_env_key(old_kek_value)
        new_kek = decode_env_key(new_kek_value)
    except ValueError as e:
        logger.error(f"Failed to decode KEK: {e}")
        return False

    logger.info(f"Old KEK decoded successfully (32 bytes)")
    logger.info(f"New KEK decoded successfully (32 bytes)")

    # Get keyring path
    data_dir = Path.home() / ".plexichat" / "data"
    keyring_path = data_dir / keyring_name

    if not keyring_path.exists():
        logger.error(f"Keyring file does not exist: {keyring_path}")
        return False

    # Perform migration
    migration = KeyringMigration(keyring_path, dry_run=dry_run)
    success = migration.migrate_kek(old_kek, new_kek, force=force)

    if success:
        logger.info("=== KEK Migration Completed Successfully ===")
    else:
        logger.info("=== KEK Migration Failed ===")

    return success


def migrate_all_keyrings(
    new_kek_env: str, force: bool = False, dry_run: bool = False
) -> bool:
    """
    Migrate all keyrings to use a new KEK.

    Args:
        new_kek_env: Environment variable name for the new KEK
        force: Force migration even if validation fails
        dry_run: Validate only without making changes

    Returns:
        True if all migrations successful, False otherwise
    """
    logger.info("=== Starting Migration of All Keyrings ===")
    logger.info(f"New KEK env var: {new_kek_env}")
    logger.info(f"Force: {force}")
    logger.info(f"Dry run: {dry_run}")

    new_kek_value = os.environ.get(new_kek_env)
    if not new_kek_value:
        logger.error(f"New KEK environment variable not set: {new_kek_env}")
        return False

    try:
        new_kek = decode_env_key(new_kek_value)
    except ValueError as e:
        logger.error(f"Failed to decode new KEK: {e}")
        return False

    logger.info(f"New KEK decoded successfully (32 bytes)")

    keyring_configs = [
        ("system_keyring.json", "PLEXICHAT_SYSTEM_KEY"),
        ("message_keyring.json", "PLEXICHAT_MESSAGE_KEY"),
        ("file_keyring.json", "PLEXICHAT_MEDIA_KEY"),
    ]

    all_success = True
    results = []

    for keyring_name, old_kek_env in keyring_configs:
        logger.info(f"\nProcessing keyring: {keyring_name}")

        old_kek_value = os.environ.get(old_kek_env)
        if not old_kek_value:
            logger.warning(
                f"Old KEK environment variable not set: {old_kek_env}, skipping keyring"
            )
            results.append((keyring_name, "skipped"))
            continue

        try:
            old_kek = decode_env_key(old_kek_value)
        except ValueError as e:
            logger.error(f"Failed to decode old KEK for {keyring_name}: {e}")
            results.append((keyring_name, "failed"))
            all_success = False
            continue

        data_dir = Path.home() / ".plexichat" / "data"
        keyring_path = data_dir / keyring_name

        if not keyring_path.exists():
            logger.warning(f"Keyring file does not exist: {keyring_path}, skipping")
            results.append((keyring_name, "skipped"))
            continue

        migration = KeyringMigration(keyring_path, dry_run=dry_run)
        success = migration.migrate_kek(old_kek, new_kek, force=force)

        results.append((keyring_name, "success" if success else "failed"))
        if not success:
            all_success = False

    # Log summary
    logger.info("\n=== Migration Summary ===")
    for keyring_name, status in results:
        logger.info(f"{keyring_name}: {status}")

    if all_success:
        logger.info("=== All Keyring Migrations Completed Successfully ===")
    else:
        logger.info("=== Some Keyring Migrations Failed ===")

    return all_success


def rollback_keyring(keyring_name: str) -> bool:
    """
    Rollback a keyring to its backup.

    Args:
        keyring_name: Name of the keyring file (e.g., "message_keyring.json")

    Returns:
        True if rollback successful, False otherwise
    """
    logger.info(f"=== Keyring Rollback Started ===")
    logger.info(f"Keyring: {keyring_name}")

    data_dir = Path.home() / ".plexichat" / "data"
    keyring_path = data_dir / keyring_name

    if not keyring_path.exists():
        logger.error(f"Keyring file does not exist: {keyring_path}")
        return False

    migration = KeyringMigration(keyring_path, dry_run=False)
    success = migration.rollback()

    if success:
        logger.info("=== Keyring Rollback Completed Successfully ===")
    else:
        logger.info("=== Keyring Rollback Failed ===")

    return success


def validate_keyrings(all_keyrings: bool = False) -> bool:
    """
    Validate that keyrings can be decrypted with their configured KEKs.

    Args:
        all_keyrings: If True, validate all keyrings. If False, validate only system keyring.

    Returns:
        True if all validations successful, False otherwise
    """
    logger.info("=== Keyring Validation Started ===")

    if all_keyrings:
        keyring_configs = [
            ("system_keyring.json", "PLEXICHAT_SYSTEM_KEY"),
            ("message_keyring.json", "PLEXICHAT_MESSAGE_KEY"),
            ("file_keyring.json", "PLEXICHAT_MEDIA_KEY"),
        ]
    else:
        keyring_configs = [("system_keyring.json", "PLEXICHAT_SYSTEM_KEY")]

    all_valid = True
    results = []

    for keyring_name, kek_env_var in keyring_configs:
        logger.info(f"\nValidating keyring: {keyring_name}")

        kek_value = os.environ.get(kek_env_var)
        if not kek_value:
            logger.warning(
                f"KEK environment variable not set: {kek_env_var}, skipping validation"
            )
            results.append((keyring_name, "skipped"))
            continue

        try:
            kek = decode_env_key(kek_value)
        except ValueError as e:
            logger.error(f"Failed to decode KEK for {keyring_name}: {e}")
            results.append((keyring_name, "failed"))
            all_valid = False
            continue

        data_dir = Path.home() / ".plexichat" / "data"
        keyring_path = data_dir / keyring_name

        if not keyring_path.exists():
            logger.warning(f"Keyring file does not exist: {keyring_path}, skipping")
            results.append((keyring_name, "skipped"))
            continue

        migration = KeyringMigration(keyring_path, dry_run=True)
        validation = migration.validate_keyring(kek, None)

        if validation["can_decrypt_with_old_kek"]:
            logger.info(
                f"Validation successful: {validation['key_count']} key version(s)"
            )
            results.append((keyring_name, "success"))
        else:
            logger.error(f"Validation failed: {validation['validation_errors']}")
            results.append((keyring_name, "failed"))
            all_valid = False

    # Log summary
    logger.info("\n=== Validation Summary ===")
    for keyring_name, status in results:
        logger.info(f"{keyring_name}: {status}")

    if all_valid:
        logger.info("=== All Validations Passed ===")
    else:
        logger.info("=== Some Validations Failed ===")

    return all_valid


def main():
    parser = argparse.ArgumentParser(
        description="KEK Migration Tool for Plexichat Encryption Keyrings",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Validate all keyrings
  python -m src.utils.encryption.kek_migration --validate --all

  # Migrate message keyring to new KEK
  python -m src.utils.encryption.kek_migration --keyring message_keyring.json --old-kek-env PLEXICHAT_SYSTEM_KEY --new-kek-env PLEXICHAT_MESSAGE_KEY

  # Migrate all keyrings to new KEK
  python -m src.utils.encryption.kek_migration --all --new-kek-env PLEXICHAT_SYSTEM_KEY

  # Rollback a migration
  python -m src.utils.encryption.kek_migration --rollback --keyring message_keyring.json

  # Dry run migration (validate only)
  python -m src.utils.encryption.kek_migration --keyring message_keyring.json --old-kek-env PLEXICHAT_SYSTEM_KEY --new-kek-env PLEXICHAT_MESSAGE_KEY --dry-run
        """,
    )

    parser.add_argument(
        "--keyring",
        help="Specific keyring file to migrate (e.g., message_keyring.json)",
    )
    parser.add_argument(
        "--old-kek-env",
        help="Environment variable name for the old KEK (e.g., PLEXICHAT_SYSTEM_KEY)",
    )
    parser.add_argument(
        "--new-kek-env",
        help="Environment variable name for the new KEK (e.g., PLEXICHAT_MESSAGE_KEY)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Migrate all keyrings to new KEK (requires --new-kek-env)",
    )
    parser.add_argument(
        "--validate", action="store_true", help="Validate keyrings without migration"
    )
    parser.add_argument(
        "--rollback", action="store_true", help="Rollback to backup keyring"
    )
    parser.add_argument(
        "--force", action="store_true", help="Force migration even if validation fails"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate only without making changes"
    )

    args = parser.parse_args()

    # Validate arguments
    if args.validate:
        if args.all:
            validate_keyrings(all_keyrings=True)
        else:
            validate_keyrings(all_keyrings=False)
        return

    if args.rollback:
        if not args.keyring:
            logger.error("--rollback requires --keyring")
            return
        rollback_keyring(args.keyring)
        return

    if args.all:
        if not args.new_kek_env:
            logger.error("--all requires --new-kek-env")
            return
        success = migrate_all_keyrings(args.new_kek_env, args.force, args.dry_run)
        sys.exit(0 if success else 1)

    if args.keyring:
        if not args.old_kek_env or not args.new_kek_env:
            logger.error("--keyring requires both --old-kek-env and --new-env-var")
            return
        success = migrate_keyring(
            args.keyring, args.old_kek_env, args.new_kek_env, args.force, args.dry_run
        )
        sys.exit(0 if success else 1)

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
