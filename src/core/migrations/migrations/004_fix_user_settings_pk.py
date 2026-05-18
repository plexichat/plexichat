"""
Fix user_settings table primary key for PostgreSQL.

Ensures the id column is a simple BIGINT (for Snowflake IDs) and not
expecting a sequence/autoincrement if it was incorrectly created.
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Apply migration to fix user_settings pk."""
    logger.info("Migration 004: Starting user_settings PK fix")
    db_type = getattr(db, "type", "sqlite")

    if db_type == "postgres":
        logger.info(
            "Migration 004: Ensuring user_settings.id is BIGINT for Snowflake IDs"
        )
        # In Postgres, if it was created as SERIAL, it has a default nextval()
        # We want to remove that default since we provide Snowflake IDs manually.
        db.execute("ALTER TABLE user_settings ALTER COLUMN id DROP DEFAULT")
        db.execute("ALTER TABLE user_settings ALTER COLUMN id TYPE BIGINT")


def down(db):
    """Rollback: Restore SERIAL default for PostgreSQL.

    For SQLite: No action needed (uses INTEGER AUTOINCREMENT).
    For PostgreSQL: Restores SERIAL default if desired.
    """
    logger.info("Migration 004 rollback: Starting rollback")
    if db.type == "postgres":
        # Note: We generally prefer Snowflake IDs, so this rollback
        # is optional. If restoring SERIAL is needed:
        try:
            db.execute(
                "ALTER TABLE user_settings ALTER COLUMN id SET DEFAULT nextval('user_settings_id_seq')"
            )
            db.execute("ALTER TABLE user_settings ALTER COLUMN id TYPE SERIAL")
            logger.info(
                "Migration 004 rollback: Restored SERIAL for user_settings.id (PostgreSQL)"
            )
        except Exception as e:
            logger.warning(f"Migration 004 rollback: Could not restore SERIAL: {e}")
    else:
        logger.info("Migration 004 rollback: No action needed for SQLite")
