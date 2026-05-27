"""
Migrate existing descriptions and topics to encrypted format.

This migration encrypts server descriptions, channel topics, thread names,
and sticker pack descriptions using the encryption utility.

Depends: 026
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Apply migration to encrypt descriptions and topics."""
    logger.info("Migration 028: Starting description encryption")

    try:
        from src.utils.encryption import encrypt_data, decrypt_data, setup
    except ImportError:
        logger.error(
            "Migration 028: Failed to import encryption utilities. "
            "Ensure encryption module is available."
        )
        raise

    # Ensure encryption is initialized
    try:
        setup()
        logger.info("Migration 028: Encryption module initialized")
    except Exception as e:
        logger.warning(
            f"Migration 028: Encryption module may already be initialized or requires setup: {e}"
        )

    # Pre-flight check: verify encryption can encrypt and decrypt a test value
    logger.info("Migration 028: Performing encryption pre-flight roundtrip check")
    try:
        test_value = "migration_028_preflight_test_value"
        encrypted_test = encrypt_data(test_value)
        decrypted_test = decrypt_data(encrypted_test)
        if decrypted_test != test_value:
            raise RuntimeError(
                f"Encryption pre-flight check failed: roundtrip mismatch "
                f"(got '{decrypted_test}', expected '{test_value}')"
            )
        logger.info("Migration 028: Encryption pre-flight roundtrip check passed")
    except ImportError:
        logger.error(
            "Migration 028: Encryption pre-flight check failed - encryption module not available. "
            "Ensure the encryption library is installed."
        )
        raise
    except RuntimeError:
        raise
    except Exception as e:
        logger.error(
            f"Migration 028: Encryption pre-flight check failed: {e}. "
            "Aborting migration to prevent data corruption. "
            "Ensure PLEXICHAT_SYSTEM_KEY (or the relevant KEK) is set in the environment."
        )
        raise

    # Server descriptions (skip if old description column doesn't exist — fresh DB)
    if db.table_exists("srv_servers") and db.column_exists(
        "srv_servers", "description"
    ):
        servers = db.fetch_all(
            "SELECT id, description FROM srv_servers WHERE description IS NOT NULL AND description_encrypted IS NULL"
        )
        server_count = len(servers)
        logger.info(
            f"Migration 028: Found {server_count} server descriptions to encrypt"
        )

        for srv in servers:
            try:
                encrypted = encrypt_data(srv["description"])
                db.execute(
                    "UPDATE srv_servers SET description_encrypted = ? WHERE id = ?",
                    (encrypted, srv["id"]),
                )
            except Exception as e:
                logger.error(
                    f"Migration 028: Failed to encrypt server {srv['id']}: {e}"
                )
                raise

        logger.info(f"Migration 028: Encrypted {server_count} server descriptions")

        # Validate encryption
        logger.info("Migration 028: Validating server description encryption")
        validation_errors = 0
        for srv in servers:
            try:
                row = db.fetch_one(
                    "SELECT description, description_encrypted FROM srv_servers WHERE id = ?",
                    (srv["id"],),
                )
                if row and row.get("description_encrypted"):
                    decrypted = decrypt_data(row["description_encrypted"])
                    if decrypted != row["description"]:
                        logger.error(
                            f"Migration 028: Validation failed for server {srv['id']}: "
                            f"decrypted data does not match original"
                        )
                        validation_errors += 1
            except Exception as e:
                logger.error(
                    f"Migration 028: Validation error for server {srv['id']}: {e}"
                )
                validation_errors += 1

        if validation_errors > 0:
            raise RuntimeError(
                f"Migration 028: Encryption validation failed with {validation_errors} errors"
            )
        logger.info("Migration 028: Server description encryption validation passed")
    elif db.table_exists("srv_servers"):
        logger.info(
            "Migration 028: 'description' column does not exist in srv_servers (fresh DB), skipping server description migration"
        )
    else:
        logger.warning("Migration 028: Table srv_servers does not exist, skipping")

    # Channel topics (skip if old topic column doesn't exist — fresh DB)
    if db.table_exists("srv_channels") and db.column_exists("srv_channels", "topic"):
        channels = db.fetch_all(
            "SELECT id, topic FROM srv_channels WHERE topic IS NOT NULL AND topic_encrypted IS NULL"
        )
        channel_count = len(channels)
        logger.info(f"Migration 028: Found {channel_count} channel topics to encrypt")

        for ch in channels:
            try:
                encrypted = encrypt_data(ch["topic"])
                db.execute(
                    "UPDATE srv_channels SET topic_encrypted = ? WHERE id = ?",
                    (encrypted, ch["id"]),
                )
            except Exception as e:
                logger.error(
                    f"Migration 028: Failed to encrypt channel {ch['id']}: {e}"
                )
                raise

        logger.info(f"Migration 028: Encrypted {channel_count} channel topics")

        # Validate encryption
        logger.info("Migration 028: Validating channel topic encryption")
        validation_errors = 0
        for ch in channels:
            try:
                row = db.fetch_one(
                    "SELECT topic, topic_encrypted FROM srv_channels WHERE id = ?",
                    (ch["id"],),
                )
                if row and row.get("topic_encrypted"):
                    decrypted = decrypt_data(row["topic_encrypted"])
                    if decrypted != row["topic"]:
                        logger.error(
                            f"Migration 028: Validation failed for channel {ch['id']}: "
                            f"decrypted data does not match original"
                        )
                        validation_errors += 1
            except Exception as e:
                logger.error(
                    f"Migration 028: Validation error for channel {ch['id']}: {e}"
                )
                validation_errors += 1

        if validation_errors > 0:
            raise RuntimeError(
                f"Migration 028: Encryption validation failed with {validation_errors} errors"
            )
        logger.info("Migration 028: Channel topic encryption validation passed")
    elif db.table_exists("srv_channels"):
        logger.info(
            "Migration 028: 'topic' column does not exist in srv_channels (fresh DB), skipping channel topic migration"
        )
    else:
        logger.warning("Migration 028: Table srv_channels does not exist, skipping")

    # Thread names
    if db.table_exists("thread_threads"):
        threads = db.fetch_all(
            "SELECT id, name FROM thread_threads WHERE name_encrypted IS NULL"
        )
        thread_count = len(threads)
        logger.info(f"Migration 028: Found {thread_count} thread names to encrypt")

        for th in threads:
            try:
                encrypted = encrypt_data(th["name"])
                db.execute(
                    "UPDATE thread_threads SET name_encrypted = ? WHERE id = ?",
                    (encrypted, th["id"]),
                )
            except Exception as e:
                logger.error(f"Migration 028: Failed to encrypt thread {th['id']}: {e}")
                raise

        logger.info(f"Migration 028: Encrypted {thread_count} thread names")

        # Validate encryption
        logger.info("Migration 028: Validating thread name encryption")
        validation_errors = 0
        for th in threads:
            try:
                row = db.fetch_one(
                    "SELECT name, name_encrypted FROM thread_threads WHERE id = ?",
                    (th["id"],),
                )
                if row and row.get("name_encrypted"):
                    decrypted = decrypt_data(row["name_encrypted"])
                    if decrypted != row["name"]:
                        logger.error(
                            f"Migration 028: Validation failed for thread {th['id']}: "
                            f"decrypted data does not match original"
                        )
                        validation_errors += 1
            except Exception as e:
                logger.error(
                    f"Migration 028: Validation error for thread {th['id']}: {e}"
                )
                validation_errors += 1

        if validation_errors > 0:
            raise RuntimeError(
                f"Migration 028: Encryption validation failed with {validation_errors} errors"
            )
        logger.info("Migration 028: Thread name encryption validation passed")
    else:
        logger.warning("Migration 028: Table thread_threads does not exist, skipping")

    # Sticker pack descriptions (skip if old description column doesn't exist — fresh DB)
    if db.table_exists("sticker_packs") and db.column_exists(
        "sticker_packs", "description"
    ):
        packs = db.fetch_all(
            "SELECT id, description FROM sticker_packs WHERE description IS NOT NULL AND description_encrypted IS NULL"
        )
        pack_count = len(packs)
        logger.info(
            f"Migration 028: Found {pack_count} sticker pack descriptions to encrypt"
        )

        for pk in packs:
            try:
                encrypted = encrypt_data(pk["description"])
                db.execute(
                    "UPDATE sticker_packs SET description_encrypted = ? WHERE id = ?",
                    (encrypted, pk["id"]),
                )
            except Exception as e:
                logger.error(
                    f"Migration 028: Failed to encrypt sticker pack {pk['id']}: {e}"
                )
                raise

        logger.info(f"Migration 028: Encrypted {pack_count} sticker pack descriptions")

        # Validate encryption
        logger.info("Migration 028: Validating sticker pack description encryption")
        validation_errors = 0
        for pk in packs:
            try:
                row = db.fetch_one(
                    "SELECT description, description_encrypted FROM sticker_packs WHERE id = ?",
                    (pk["id"],),
                )
                if row and row.get("description_encrypted"):
                    decrypted = decrypt_data(row["description_encrypted"])
                    if decrypted != row["description"]:
                        logger.error(
                            f"Migration 028: Validation failed for sticker pack {pk['id']}: "
                            f"decrypted data does not match original"
                        )
                        validation_errors += 1
            except Exception as e:
                logger.error(
                    f"Migration 028: Validation error for sticker pack {pk['id']}: {e}"
                )
                validation_errors += 1

        if validation_errors > 0:
            raise RuntimeError(
                f"Migration 028: Encryption validation failed with {validation_errors} errors"
            )
        logger.info(
            "Migration 028: Sticker pack description encryption validation passed"
        )
    elif db.table_exists("sticker_packs"):
        logger.info(
            "Migration 028: 'description' column does not exist in sticker_packs (fresh DB), skipping sticker pack description migration"
        )
    else:
        logger.warning("Migration 028: Table sticker_packs does not exist, skipping")

    logger.info("Migration 028: Description encryption completed successfully")


def down(db):
    """Rollback: clear encrypted columns."""
    logger.info("Migration 027 rollback: Clearing encrypted columns")

    if db.table_exists("srv_servers"):
        db.execute("UPDATE srv_servers SET description_encrypted = NULL")

    if db.table_exists("srv_channels"):
        db.execute("UPDATE srv_channels SET topic_encrypted = NULL")

    if db.table_exists("thread_threads"):
        db.execute("UPDATE thread_threads SET name_encrypted = NULL")

    if db.table_exists("sticker_packs"):
        db.execute("UPDATE sticker_packs SET description_encrypted = NULL")

    logger.info("Migration 027 rollback: Completed")
