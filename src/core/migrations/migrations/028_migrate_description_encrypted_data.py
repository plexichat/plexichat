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
    logger.info("Migration 027: Starting description encryption")

    try:
        from src.utils.encryption import encrypt_data, decrypt_data, setup
    except ImportError:
        logger.error(
            "Migration 027: Failed to import encryption utilities. "
            "Ensure encryption module is available."
        )
        raise

    # Ensure encryption is initialized
    try:
        setup()
        logger.info("Migration 027: Encryption module initialized")
    except Exception as e:
        logger.warning(
            f"Migration 027: Encryption module may already be initialized or requires setup: {e}"
        )

    # Server descriptions
    if db.table_exists("srv_servers"):
        servers = db.fetch_all(
            "SELECT id, description FROM srv_servers WHERE description IS NOT NULL AND description_encrypted IS NULL"
        )
        server_count = len(servers)
        logger.info(
            f"Migration 027: Found {server_count} server descriptions to encrypt"
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
                    f"Migration 027: Failed to encrypt server {srv['id']}: {e}"
                )
                raise

        logger.info(f"Migration 027: Encrypted {server_count} server descriptions")

        # Validate encryption
        logger.info("Migration 027: Validating server description encryption")
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
                            f"Migration 027: Validation failed for server {srv['id']}: "
                            f"decrypted data does not match original"
                        )
                        validation_errors += 1
            except Exception as e:
                logger.error(
                    f"Migration 027: Validation error for server {srv['id']}: {e}"
                )
                validation_errors += 1

        if validation_errors > 0:
            raise RuntimeError(
                f"Migration 027: Encryption validation failed with {validation_errors} errors"
            )
        logger.info("Migration 027: Server description encryption validation passed")
    else:
        logger.warning("Migration 027: Table srv_servers does not exist, skipping")

    # Channel topics
    if db.table_exists("srv_channels"):
        channels = db.fetch_all(
            "SELECT id, topic FROM srv_channels WHERE topic IS NOT NULL AND topic_encrypted IS NULL"
        )
        channel_count = len(channels)
        logger.info(f"Migration 027: Found {channel_count} channel topics to encrypt")

        for ch in channels:
            try:
                encrypted = encrypt_data(ch["topic"])
                db.execute(
                    "UPDATE srv_channels SET topic_encrypted = ? WHERE id = ?",
                    (encrypted, ch["id"]),
                )
            except Exception as e:
                logger.error(
                    f"Migration 027: Failed to encrypt channel {ch['id']}: {e}"
                )
                raise

        logger.info(f"Migration 027: Encrypted {channel_count} channel topics")

        # Validate encryption
        logger.info("Migration 027: Validating channel topic encryption")
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
                            f"Migration 027: Validation failed for channel {ch['id']}: "
                            f"decrypted data does not match original"
                        )
                        validation_errors += 1
            except Exception as e:
                logger.error(
                    f"Migration 027: Validation error for channel {ch['id']}: {e}"
                )
                validation_errors += 1

        if validation_errors > 0:
            raise RuntimeError(
                f"Migration 027: Encryption validation failed with {validation_errors} errors"
            )
        logger.info("Migration 027: Channel topic encryption validation passed")
    else:
        logger.warning("Migration 027: Table srv_channels does not exist, skipping")

    # Thread names
    if db.table_exists("thread_threads"):
        threads = db.fetch_all(
            "SELECT id, name FROM thread_threads WHERE name_encrypted IS NULL"
        )
        thread_count = len(threads)
        logger.info(f"Migration 027: Found {thread_count} thread names to encrypt")

        for th in threads:
            try:
                encrypted = encrypt_data(th["name"])
                db.execute(
                    "UPDATE thread_threads SET name_encrypted = ? WHERE id = ?",
                    (encrypted, th["id"]),
                )
            except Exception as e:
                logger.error(f"Migration 027: Failed to encrypt thread {th['id']}: {e}")
                raise

        logger.info(f"Migration 027: Encrypted {thread_count} thread names")

        # Validate encryption
        logger.info("Migration 027: Validating thread name encryption")
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
                            f"Migration 027: Validation failed for thread {th['id']}: "
                            f"decrypted data does not match original"
                        )
                        validation_errors += 1
            except Exception as e:
                logger.error(
                    f"Migration 027: Validation error for thread {th['id']}: {e}"
                )
                validation_errors += 1

        if validation_errors > 0:
            raise RuntimeError(
                f"Migration 027: Encryption validation failed with {validation_errors} errors"
            )
        logger.info("Migration 027: Thread name encryption validation passed")
    else:
        logger.warning("Migration 027: Table thread_threads does not exist, skipping")

    # Sticker pack descriptions
    if db.table_exists("sticker_packs"):
        packs = db.fetch_all(
            "SELECT id, description FROM sticker_packs WHERE description IS NOT NULL AND description_encrypted IS NULL"
        )
        pack_count = len(packs)
        logger.info(
            f"Migration 027: Found {pack_count} sticker pack descriptions to encrypt"
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
                    f"Migration 027: Failed to encrypt sticker pack {pk['id']}: {e}"
                )
                raise

        logger.info(f"Migration 027: Encrypted {pack_count} sticker pack descriptions")

        # Validate encryption
        logger.info("Migration 027: Validating sticker pack description encryption")
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
                            f"Migration 027: Validation failed for sticker pack {pk['id']}: "
                            f"decrypted data does not match original"
                        )
                        validation_errors += 1
            except Exception as e:
                logger.error(
                    f"Migration 027: Validation error for sticker pack {pk['id']}: {e}"
                )
                validation_errors += 1

        if validation_errors > 0:
            raise RuntimeError(
                f"Migration 027: Encryption validation failed with {validation_errors} errors"
            )
        logger.info(
            "Migration 027: Sticker pack description encryption validation passed"
        )
    else:
        logger.warning("Migration 027: Table sticker_packs does not exist, skipping")

    logger.info("Migration 027: Description encryption completed successfully")


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
