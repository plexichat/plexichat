"""
Admin Notes Versioning Migration - Adds versioning and markdown support to internal notes.

This migration adds:
- admin_notes_versioning table for tracking note changes
- markdown support flag
- version history for admin notes
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Apply the migration."""
    logger.info("Migration 033: Starting Admin Notes Versioning")

    # === Admin Notes Versioning Table ===
    db.execute("""
        CREATE TABLE IF NOT EXISTS admin_notes_versioning (
            id INTEGER PRIMARY KEY,
            target_type VARCHAR(50) NOT NULL,
            target_id INTEGER NOT NULL,
            note_content TEXT NOT NULL,
            note_format VARCHAR(20) DEFAULT 'plain',
            created_by INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            version_number INTEGER NOT NULL,
            change_reason TEXT
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_notes_target ON admin_notes_versioning(target_type, target_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_notes_created ON admin_notes_versioning(created_at)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_notes_author ON admin_notes_versioning(created_by)"
    )

    # === Add markdown support columns ===
    from src.core.database import dialect

    for table_name in ("auth_users", "feedback"):
        if not db.table_exists(table_name):
            continue
        if db.column_exists(table_name, "internal_notes_format"):
            continue
        safe_table = dialect.sanitize_identifier(table_name, db.type)
        try:
            db.execute(
                f"ALTER TABLE {safe_table} ADD COLUMN internal_notes_format VARCHAR(20) DEFAULT 'plain'"
            )
        except Exception as e:
            logger.debug(
                "Migration 034: %s.internal_notes_format already present (%s)",
                table_name,
                e,
            )

    logger.info("Migration 033: Admin Notes Versioning completed successfully")


def down(db):
    """Rollback the migration."""
    logger.info("Migration 033 rollback: Starting rollback")

    # Drop versioning table
    if db.table_exists("admin_notes_versioning"):
        db.execute("DROP TABLE IF EXISTS admin_notes_versioning")

    # For PostgreSQL, drop the added columns
    if db.type == "postgres":
        try:
            from src.core.database import dialect

            safe_auth_table = dialect.sanitize_identifier("auth_users", db.type)
            safe_feedback_table = dialect.sanitize_identifier("feedback", db.type)

            if db.column_exists("auth_users", "internal_notes_format"):
                db.execute(
                    f"ALTER TABLE {safe_auth_table} DROP COLUMN internal_notes_format"
                )
            if db.column_exists("feedback", "internal_notes_format"):
                db.execute(
                    f"ALTER TABLE {safe_feedback_table} DROP COLUMN internal_notes_format"
                )

            logger.info(
                "Migration 033 rollback: Dropped table and columns (PostgreSQL)"
            )
        except Exception as e:
            logger.warning(f"Migration 033 rollback error: {e}")
    else:
        # SQLite: Clear column values but leave columns
        try:
            if db.column_exists("auth_users", "internal_notes_format"):
                db.execute("UPDATE auth_users SET internal_notes_format = 'plain'")
            if db.column_exists("feedback", "internal_notes_format"):
                db.execute("UPDATE feedback SET internal_notes_format = 'plain'")

            logger.info(
                "Migration 033 rollback: Dropped table, cleared column values (SQLite)"
            )
        except Exception as e:
            logger.warning(f"Migration 033 rollback error: {e}")

    logger.info("Migration 033 rollback completed")
