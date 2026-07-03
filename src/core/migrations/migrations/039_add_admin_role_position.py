"""
Add position column to admin_roles for hierarchy enforcement.

This migration adds:
- position column to admin_roles with default values based on role type
- Admin role hierarchy enforcement enables lower-ranked admins from modifying
  higher-ranked ones
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Apply the migration."""
    logger.info("Migration 039: Adding position column to admin_roles")

    # Add position column to admin_roles
    try:
        if not db.column_exists("admin_roles", "position"):
            db.execute("""
                ALTER TABLE admin_roles
                ADD COLUMN position INTEGER NOT NULL DEFAULT 10
            """)
            logger.info("Migration 039: Added position column")
    except Exception as e:
        logger.warning(f"Migration 039: Could not add position column: {e}")

    # Set default positions for system roles based on their permission level
    # Super Admin = 100, Standard = 60, Support = 40, Read-Only = 10
    try:
        db.execute("""
            UPDATE admin_roles SET position = 100
            WHERE name = 'super_admin' AND is_system = 1
        """)
        db.execute("""
            UPDATE admin_roles SET position = 60
            WHERE name IN ('moderation_admin', 'support_admin') AND is_system = 1
        """)
        db.execute("""
            UPDATE admin_roles SET position = 10
            WHERE name = 'readonly_admin' AND is_system = 1
        """)
        logger.info("Migration 039: Set default positions for system roles")
    except Exception as e:
        logger.warning(f"Migration 039: Could not set default positions: {e}")

    # Create admin_approval_comments table for approval workflow comments
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS admin_approval_comments (
                id BIGINT PRIMARY KEY,
                approval_id BIGINT NOT NULL,
                admin_id BIGINT NOT NULL,
                comment TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (approval_id) REFERENCES admin_approvals(id) ON DELETE CASCADE
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_admin_approval_comments_approval
            ON admin_approval_comments(approval_id)
        """)
        logger.info("Migration 039: Created admin_approval_comments table")
    except Exception as e:
        logger.warning(
            f"Migration 039: Could not create admin_approval_comments table: {e}"
        )

    logger.info("Migration 039 completed successfully")


def down(db):
    """Rollback the migration."""
    logger.info("Migration 039 rollback: Starting rollback")

    try:
        if db.column_exists("admin_roles", "position"):
            if db.type == "postgres":
                db.execute("ALTER TABLE admin_roles DROP COLUMN position")
            else:
                # SQLite doesn't support DROP COLUMN easily
                logger.info(
                    "Migration 039 rollback: position column left in place (SQLite)"
                )
    except Exception as e:
        logger.warning(f"Migration 039 rollback error: {e}")

    # Drop admin_approval_comments table
    try:
        if db.type == "postgres":
            db.execute("DROP TABLE IF EXISTS admin_approval_comments")
        else:
            # SQLite
            db.execute("DROP TABLE IF EXISTS admin_approval_comments")
        logger.info("Migration 039 rollback: Dropped admin_approval_comments table")
    except Exception as e:
        logger.warning(
            f"Migration 039 rollback: Could not drop admin_approval_comments: {e}"
        )

    logger.info("Migration 039 rollback completed")
