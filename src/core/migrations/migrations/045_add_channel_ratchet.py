"""
Add channel ratchet intervals table for the v3 message ratchet.

Creates ``channel_ratchet_intervals`` to store per-channel
encryption-key ranges, and adds ``ratchet_interval_id`` to
``msg_messages`` so each message row remembers which interval
encrypted it.

Version: 045
Depends: 044
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    logger.info("Migration 045: Starting channel ratchet tables")

    if not db.table_exists("channel_ratchet_intervals"):
        try:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_ratchet_intervals (
                    interval_id BIGINT PRIMARY KEY,
                    conversation_id BIGINT NOT NULL,
                    start_message_id BIGINT NOT NULL,
                    end_message_id BIGINT,
                    start_key_wrapped TEXT NOT NULL,
                    created_at BIGINT NOT NULL,
                    last_message_at BIGINT NOT NULL
                )
                """
            )
            logger.info("Migration 045: Created channel_ratchet_intervals table")
        except Exception as e:
            logger.warning(
                "Migration 045: Failed to create channel_ratchet_intervals table: %s",
                e,
            )
    else:
        logger.info("Migration 045: channel_ratchet_intervals already exists, skipping")

    try:
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_channel_ratchet_intervals_conversation
            ON channel_ratchet_intervals(conversation_id, start_message_id)
            """
        )
        logger.info("Migration 045: Created idx_channel_ratchet_intervals_conversation")
    except Exception as e:
        logger.warning(
            "Migration 045: Failed to create idx_channel_ratchet_intervals_conversation: %s",
            e,
        )

    try:
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_channel_ratchet_intervals_open
            ON channel_ratchet_intervals(conversation_id, end_message_id)
            """
        )
        logger.info("Migration 045: Created idx_channel_ratchet_intervals_open")
    except Exception as e:
        logger.warning(
            "Migration 045: Failed to create idx_channel_ratchet_intervals_open: %s",
            e,
        )

    if db.table_exists("msg_messages"):
        try:
            existing_cols = {
                row["name"] if isinstance(row, dict) else row[1]
                for row in db.fetch_all("PRAGMA table_info(msg_messages)")
            }
        except Exception:
            existing_cols = set()

        if "ratchet_interval_id" not in existing_cols:
            try:
                db.execute(
                    """
                    ALTER TABLE msg_messages
                    ADD COLUMN ratchet_interval_id BIGINT
                    """
                )
                logger.info(
                    "Migration 045: Added ratchet_interval_id column to msg_messages"
                )
            except Exception as e:
                logger.warning(
                    "Migration 045: Failed to add ratchet_interval_id column: %s",
                    e,
                )
        else:
            logger.info(
                "Migration 045: msg_messages.ratchet_interval_id already exists"
            )

        try:
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_msg_messages_ratchet_interval_id
                ON msg_messages(ratchet_interval_id)
                """
            )
            logger.info("Migration 045: Created idx_msg_messages_ratchet_interval_id")
        except Exception as e:
            logger.warning(
                "Migration 045: Failed to create idx_msg_messages_ratchet_interval_id: %s",
                e,
            )
    else:
        logger.warning(
            "Migration 045: msg_messages table not found, skipping column add"
        )

    logger.info("Migration 045 completed successfully")


def down(db):
    """Rollback the migration."""
    logger.info("Migration 045 rollback: starting")

    try:
        db.execute("DROP INDEX IF EXISTS idx_msg_messages_ratchet_interval_id")
        logger.info(
            "Migration 045 rollback: dropped idx_msg_messages_ratchet_interval_id"
        )
    except Exception as e:
        logger.warning(
            "Migration 045 rollback: failed to drop idx_msg_messages_ratchet_interval_id: %s",
            e,
        )

    if db.table_exists("msg_messages"):
        try:
            existing_cols = {
                row["name"] if isinstance(row, dict) else row[1]
                for row in db.fetch_all("PRAGMA table_info(msg_messages)")
            }
        except Exception:
            existing_cols = set()

        if "ratchet_interval_id" in existing_cols:
            try:
                db.execute("ALTER TABLE msg_messages DROP COLUMN ratchet_interval_id")
                logger.info(
                    "Migration 045 rollback: dropped ratchet_interval_id column"
                )
            except Exception as e:
                logger.warning(
                    "Migration 045 rollback: failed to drop ratchet_interval_id: %s",
                    e,
                )

    try:
        db.execute("DROP INDEX IF EXISTS idx_channel_ratchet_intervals_open")
        logger.info(
            "Migration 045 rollback: dropped idx_channel_ratchet_intervals_open"
        )
    except Exception as e:
        logger.warning(
            "Migration 045 rollback: failed to drop idx_channel_ratchet_intervals_open: %s",
            e,
        )

    try:
        db.execute("DROP INDEX IF EXISTS idx_channel_ratchet_intervals_conversation")
        logger.info(
            "Migration 045 rollback: dropped idx_channel_ratchet_intervals_conversation"
        )
    except Exception as e:
        logger.warning(
            "Migration 045 rollback: failed to drop idx_channel_ratchet_intervals_conversation: %s",
            e,
        )

    if db.table_exists("channel_ratchet_intervals"):
        try:
            db.execute("DROP TABLE IF EXISTS channel_ratchet_intervals")
            logger.info("Migration 045 rollback: dropped channel_ratchet_intervals")
        except Exception as e:
            logger.warning(
                "Migration 045 rollback: failed to drop channel_ratchet_intervals: %s",
                e,
            )

    logger.info("Migration 045 rollback completed")
