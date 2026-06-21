"""
Migrate existing poll questions and options to encrypted format.

This migration encrypts all existing poll questions and options using the
encryption utility. Original data is preserved in the unencrypted columns
for backwards compatibility.

Depends: 024
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Apply migration to encrypt poll questions and options."""
    logger.info("Migration 025: Starting poll data encryption")

    # Import encryption utilities
    try:
        from src.utils.encryption import encrypt_data, decrypt_data, setup
    except ImportError:
        logger.error(
            "Migration 025: Failed to import encryption utilities. "
            "Ensure encryption module is available."
        )
        raise

    # Ensure encryption is initialized
    try:
        setup()
        logger.info("Migration 025: Encryption module initialized")
    except Exception as e:
        logger.warning(
            f"Migration 025: Encryption module may already be initialized or requires setup: {e}"
        )

    # Migrate poll questions
    if db.table_exists("poll_polls"):
        polls = db.fetch_all(
            "SELECT id, question FROM poll_polls WHERE question_encrypted IS NULL"
        )
        poll_count = len(polls)
        logger.info(f"Migration 025: Found {poll_count} poll questions to encrypt")

        for poll in polls:
            try:
                encrypted = encrypt_data(poll["question"])
                db.execute(
                    "UPDATE poll_polls SET question_encrypted = ? WHERE id = ?",
                    (encrypted, poll["id"]),
                )
            except Exception as e:
                logger.error(f"Migration 025: Failed to encrypt poll {poll['id']}: {e}")
                raise

        logger.info(f"Migration 025: Encrypted {poll_count} poll questions")

        # Validate encryption by attempting to decrypt
        logger.info("Migration 025: Validating poll question encryption")
        validation_errors = 0
        for poll in polls:
            try:
                row = db.fetch_one(
                    "SELECT question, question_encrypted FROM poll_polls WHERE id = ?",
                    (poll["id"],),
                )
                if row and row.get("question_encrypted"):
                    decrypted = decrypt_data(row["question_encrypted"])
                    if decrypted != row["question"]:
                        logger.error(
                            f"Migration 025: Validation failed for poll {poll['id']}: "
                            f"decrypted data does not match original"
                        )
                        validation_errors += 1
            except Exception as e:
                logger.error(
                    f"Migration 025: Validation error for poll {poll['id']}: {e}"
                )
                validation_errors += 1

        if validation_errors > 0:
            raise RuntimeError(
                f"Migration 025: Encryption validation failed with {validation_errors} errors"
            )
        logger.info("Migration 025: Poll question encryption validation passed")
    else:
        logger.warning("Migration 025: Table poll_polls does not exist, skipping")

    # Migrate poll options
    if db.table_exists("poll_options"):
        options = db.fetch_all(
            "SELECT id, text FROM poll_options WHERE text_encrypted IS NULL"
        )
        option_count = len(options)
        logger.info(f"Migration 025: Found {option_count} poll options to encrypt")

        for opt in options:
            try:
                encrypted = encrypt_data(opt["text"])
                db.execute(
                    "UPDATE poll_options SET text_encrypted = ? WHERE id = ?",
                    (encrypted, opt["id"]),
                )
            except Exception as e:
                logger.error(
                    f"Migration 025: Failed to encrypt option {opt['id']}: {e}"
                )
                raise

        logger.info(f"Migration 025: Encrypted {option_count} poll options")

        # Validate encryption by attempting to decrypt
        logger.info("Migration 025: Validating poll option encryption")
        validation_errors = 0
        for opt in options:
            try:
                row = db.fetch_one(
                    "SELECT text, text_encrypted FROM poll_options WHERE id = ?",
                    (opt["id"],),
                )
                if row and row.get("text_encrypted"):
                    decrypted = decrypt_data(row["text_encrypted"])
                    if decrypted != row["text"]:
                        logger.error(
                            f"Migration 025: Validation failed for option {opt['id']}: "
                            f"decrypted data does not match original"
                        )
                        validation_errors += 1
            except Exception as e:
                logger.error(
                    f"Migration 025: Validation error for option {opt['id']}: {e}"
                )
                validation_errors += 1

        if validation_errors > 0:
            raise RuntimeError(
                f"Migration 025: Encryption validation failed with {validation_errors} errors"
            )
        logger.info("Migration 025: Poll option encryption validation passed")
    else:
        logger.warning("Migration 025: Table poll_options does not exist, skipping")

    logger.info("Migration 025: Poll data encryption completed successfully")


def down(db):
    """Rollback: clear encrypted columns (data preserved in original columns)."""
    logger.info("Migration 025 rollback: Clearing encrypted columns")

    if db.table_exists("poll_polls"):
        db.execute("UPDATE poll_polls SET question_encrypted = NULL")
        logger.info("Migration 025 rollback: Cleared question_encrypted")

    if db.table_exists("poll_options"):
        db.execute("UPDATE poll_options SET text_encrypted = NULL")
        logger.info("Migration 025 rollback: Cleared text_encrypted")

    logger.info("Migration 025 rollback: Completed")
