"""
Add encrypted columns to app_webhook_deliveries.

Adds:
- app_webhook_deliveries.request_body_encrypted TEXT
- app_webhook_deliveries.response_body_encrypted TEXT

The original request_body and response_body columns are kept for
backwards-compatible reads. New writes go to the *_encrypted columns.

Depends: 039

Version: 040
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Apply the migration."""
    logger.info("Migration 040: Adding encrypted webhook delivery columns")

    if not db.table_exists("app_webhook_deliveries"):
        logger.warning(
            "Migration 040: app_webhook_deliveries table does not exist, skipping"
        )
        return

    try:
        if not db.column_exists("app_webhook_deliveries", "request_body_encrypted"):
            db.execute(
                "ALTER TABLE app_webhook_deliveries ADD COLUMN request_body_encrypted TEXT"
            )
            logger.info("Migration 040: Added request_body_encrypted")
        else:
            logger.info(
                "Migration 040: request_body_encrypted already exists, skipping"
            )
    except Exception as e:
        logger.warning(f"Migration 040: Could not add request_body_encrypted: {e}")

    try:
        if not db.column_exists("app_webhook_deliveries", "response_body_encrypted"):
            db.execute(
                "ALTER TABLE app_webhook_deliveries ADD COLUMN response_body_encrypted TEXT"
            )
            logger.info("Migration 040: Added response_body_encrypted")
        else:
            logger.info(
                "Migration 040: response_body_encrypted already exists, skipping"
            )
    except Exception as e:
        logger.warning(f"Migration 040: Could not add response_body_encrypted: {e}")

    logger.info("Migration 040 completed successfully")


def down(db):
    """Rollback the migration."""
    logger.info("Migration 040 rollback: Starting rollback")

    try:
        if db.type == "postgres":
            if db.column_exists("app_webhook_deliveries", "request_body_encrypted"):
                db.execute(
                    "ALTER TABLE app_webhook_deliveries DROP COLUMN request_body_encrypted"
                )
            if db.column_exists("app_webhook_deliveries", "response_body_encrypted"):
                db.execute(
                    "ALTER TABLE app_webhook_deliveries DROP COLUMN response_body_encrypted"
                )
        else:
            logger.info(
                "Migration 040 rollback: ADD COLUMN not reversible in SQLite "
                "(column left in place)"
            )
    except Exception as e:
        logger.warning(f"Migration 040 rollback error: {e}")

    logger.info("Migration 040 rollback completed")
