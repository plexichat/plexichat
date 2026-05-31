"""
CLI functions for KEK migration operations.

Provides the command-line interface for migrating, validating, and rolling back
encryption keyrings.
"""

import os
import sys
import argparse
import logging
from pathlib import Path
from typing import List, Tuple

from .migration import KeyringMigration
from .utils import decode_env_key

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


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
    logger.info("=== KEK Migration Started ===")
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

    logger.info("Old KEK decoded successfully (32 bytes)")
    logger.info("New KEK decoded successfully (32 bytes)")

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

    logger.info("New KEK decoded successfully (32 bytes)")

    keyring_configs: List[Tuple[str, str]] = [
        ("system_keyring.json", "PLEXICHAT_SYSTEM_KEY"),
        ("message_keyring.json", "PLEXICHAT_MESSAGE_KEY"),
        ("file_keyring.json", "PLEXICHAT_MEDIA_KEY"),
    ]

    all_success = True
    results: List[Tuple[str, str]] = []

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
    logger.info("=== Keyring Rollback Started ===")
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
        keyring_configs: List[Tuple[str, str]] = [
            ("system_keyring.json", "PLEXICHAT_SYSTEM_KEY"),
            ("message_keyring.json", "PLEXICHAT_MESSAGE_KEY"),
            ("file_keyring.json", "PLEXICHAT_MEDIA_KEY"),
        ]
    else:
        keyring_configs = [("system_keyring.json", "PLEXICHAT_SYSTEM_KEY")]

    all_valid = True
    results: List[Tuple[str, str]] = []

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
        epilog="""Examples:
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
