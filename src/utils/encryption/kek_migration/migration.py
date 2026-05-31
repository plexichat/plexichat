"""
Core KeyringMigration class for KEK (Key Encryption Key) Migration.

Handles secure re-encryption of keyrings with new KEKs, with comprehensive logging,
rollback support, and secure cleanup of temporary files.
"""

import os
import json
import shutil
import base64
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .protocol import KEKMigrationProtocol


logger = logging.getLogger(__name__)


class KEKMigrationError(Exception):
    """Raised when KEK migration fails."""

    pass


class KeyringMigration(KEKMigrationProtocol):
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

            # Validate the migration (new_kek is the first arg → "old_kek" param in validate_keyring)
            post_validation = self.validate_keyring(new_kek)
            if not post_validation["can_decrypt_with_old_kek"]:
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
                except Exception:
                    pass
                try:
                    # Try hex
                    decoded = bytes.fromhex(env_value)
                    if decoded == kek:
                        return f"Environment variable {env_var} (hex)"
                except Exception:
                    pass

        # Check if it matches machine key file
        try:
            machine_key_path = Path.home() / ".plexichat" / "data" / ".machine_key"
            if machine_key_path.exists():
                if machine_key_path.read_bytes() == kek:
                    return "Machine-local key file"
        except Exception:
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
