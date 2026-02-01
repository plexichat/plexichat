"""
Migration: Add index to msg_message_status for faster reader ID lookups.

Description:
    Creates an index on (message_id, status) in the msg_message_status table.
    This significantly improves the performance of get_batch_reader_ids which is used 
    during message loading to show who read each message.
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Apply the migration."""
    logger.info("Migration 006: Creating index idx_msg_status_msg_status on msg_message_status")
    
    # We use message_id and status because we often filter by both
    # (e.g. SELECT user_id FROM msg_message_status WHERE message_id IN (...) AND status = 'read')
    db.execute("CREATE INDEX IF NOT EXISTS idx_msg_status_msg_status ON msg_message_status(message_id, status)")


def down(db):
    """Rollback migration."""
    logger.info("Migration 006: Dropping index idx_msg_status_msg_status")
    db.execute("DROP INDEX IF EXISTS idx_msg_status_msg_status")
