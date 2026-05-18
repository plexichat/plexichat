"""
Migrate internal notes to encrypted format.

This migration encrypts user internal notes and feedback internal notes
using the encryption utility.

Depends: 029
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Apply migration to encrypt internal notes."""
    logger.info("Migration 030: Starting internal notes encryption")

    try:
        from src.utils.encryption import encrypt_data, decrypt_data, setup
    except ImportError:
        logger.error(
            "Migration 030: Failed to import encryption utilities. "
            "Ensure encryption module is available."
        )
        raise

    # Ensure encryption is initialized
    try:
        setup()
        logger.info("Migration 030: Encryption module initialized")
    except Exception as e:
        logger.warning(
            f"Migration 030: Encryption module may already be initialized or requires setup: {e}"
        )

    # User internal notes
    if db.table_exists("auth_users"):
        notes = db.fetch_all(
            "SELECT id, internal_notes FROM auth_users WHERE internal_notes IS NOT NULL AND internal_notes_encrypted IS NULL"
        )
        note_count = len(notes)
        logger.info(f"Migration 030: Found {note_count} user internal notes to encrypt")

        for note in notes:
            try:
                encrypted = encrypt_data(note["internal_notes"])
                db.execute(
                    "UPDATE auth_users SET internal_notes_encrypted = ? WHERE id = ?",
                    (encrypted, note["id"]),
                )
            except Exception as e:
                logger.error(
                    f"Migration 030: Failed to encrypt user notes {note['id']}: {e}"
                )
                raise

        logger.info(f"Migration 030: Encrypted {note_count} user internal notes")

        # Validate encryption
        logger.info("Migration 030: Validating user internal notes encryption")
        validation_errors = 0
        for note in notes:
            try:
                row = db.fetch_one(
                    "SELECT internal_notes, internal_notes_encrypted FROM auth_users WHERE id = ?",
                    (note["id"],),
                )
                if row and row.get("internal_notes_encrypted"):
                    decrypted = decrypt_data(row["internal_notes_encrypted"])
                    if decrypted != row["internal_notes"]:
                        logger.error(
                            f"Migration 030: Validation failed for user {note['id']}: "
                            f"decrypted data does not match original"
                        )
                        validation_errors += 1
            except Exception as e:
                logger.error(
                    f"Migration 030: Validation error for user {note['id']}: {e}"
                )
                validation_errors += 1

        if validation_errors > 0:
            raise RuntimeError(
                f"Migration 030: Encryption validation failed with {validation_errors} errors"
            )
        logger.info("Migration 030: User internal notes encryption validation passed")
    else:
        logger.warning("Migration 030: Table auth_users does not exist, skipping")

    # Feedback internal notes
    if db.table_exists("feedback"):
        feedback = db.fetch_all(
            "SELECT id, internal_notes FROM feedback WHERE internal_notes IS NOT NULL AND internal_notes_encrypted IS NULL"
        )
        feedback_count = len(feedback)
        logger.info(
            f"Migration 030: Found {feedback_count} feedback internal notes to encrypt"
        )

        for fb in feedback:
            try:
                encrypted = encrypt_data(fb["internal_notes"])
                db.execute(
                    "UPDATE feedback SET internal_notes_encrypted = ? WHERE id = ?",
                    (encrypted, fb["id"]),
                )
            except Exception as e:
                logger.error(
                    f"Migration 030: Failed to encrypt feedback {fb['id']}: {e}"
                )
                raise

        logger.info(
            f"Migration 030: Encrypted {feedback_count} feedback internal notes"
        )

        # Validate encryption
        logger.info("Migration 030: Validating feedback internal notes encryption")
        validation_errors = 0
        for fb in feedback:
            try:
                row = db.fetch_one(
                    "SELECT internal_notes, internal_notes_encrypted FROM feedback WHERE id = ?",
                    (fb["id"],),
                )
                if row and row.get("internal_notes_encrypted"):
                    decrypted = decrypt_data(row["internal_notes_encrypted"])
                    if decrypted != row["internal_notes"]:
                        logger.error(
                            f"Migration 030: Validation failed for feedback {fb['id']}: "
                            f"decrypted data does not match original"
                        )
                        validation_errors += 1
            except Exception as e:
                logger.error(
                    f"Migration 030: Validation error for feedback {fb['id']}: {e}"
                )
                validation_errors += 1

        if validation_errors > 0:
            raise RuntimeError(
                f"Migration 030: Encryption validation failed with {validation_errors} errors"
            )
        logger.info(
            "Migration 030: Feedback internal notes encryption validation passed"
        )
    else:
        logger.warning("Migration 030: Table feedback does not exist, skipping")

    logger.info("Migration 030: Internal notes encryption completed successfully")


def down(db):
    """Rollback: clear encrypted columns."""
    logger.info("Migration 030 rollback: Clearing encrypted columns")

    if db.table_exists("auth_users"):
        db.execute("UPDATE auth_users SET internal_notes_encrypted = NULL")

    if db.table_exists("feedback"):
        db.execute("UPDATE feedback SET internal_notes_encrypted = NULL")

    logger.info("Migration 030 rollback: Completed")
