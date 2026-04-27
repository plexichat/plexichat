"""
Fix last-chat tables to use BIGINT snowflake-compatible identifiers.

Older deployments created `user_last_chat` and `user_recent_chats` with INTEGER
conversation/message columns, which overflows for production snowflake IDs.
"""

import logging

logger = logging.getLogger(__name__)


def _alter_postgres_column_type(db, table: str, column: str, target_type: str) -> None:
    logger.debug(
        f"Checking column {column} in table {table} for type change to {target_type}"
    )
    rows = db.fetch_all(
        """
        SELECT data_type
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = ?
          AND column_name = ?
        """,
        (table, column),
    )
    if not rows:
        return

    current_type = (rows[0].get("data_type") or "").lower()
    if current_type == target_type.lower():
        return

    db.execute(
        f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {target_type} USING {column}::{target_type}"
    )


def up(db):
    """Upgrade last-chat tables to BIGINT identifiers on existing installs."""
    logger.info("Migration 020: Starting last-chat BIGINT fix (PostgreSQL)")
    if db.type != "postgres":
        return

    _alter_postgres_column_type(db, "user_last_chat", "id", "BIGINT")
    _alter_postgres_column_type(db, "user_last_chat", "user_id", "BIGINT")
    _alter_postgres_column_type(db, "user_last_chat", "conversation_id", "BIGINT")
    _alter_postgres_column_type(db, "user_last_chat", "last_message_id", "BIGINT")


def down(db):
    """Rollback: No-op - shrinking columns is unsafe.

    This migration changes INTEGER to BIGINT for Snowflake ID support.
    Reverting could cause data truncation, so we don't support rollback.
    """
    logger.info("Migration 020 rollback: No-op (shrinking BIGINT to INTEGER is unsafe)")
    return
