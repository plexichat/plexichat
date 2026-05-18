"""
Add API access tokens table for authentication.
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    try:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_api_access_tokens (
                id BIGINT PRIMARY KEY,
                name TEXT,
                token_index TEXT UNIQUE NOT NULL,
                token_encrypted TEXT NOT NULL,
                created_by BIGINT,
                created_at BIGINT NOT NULL,
                last_used_at BIGINT,
                revoked INTEGER DEFAULT 0,
                revoked_at BIGINT,
                revoked_by BIGINT
            )
            """
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_api_access_tokens_index ON auth_api_access_tokens(token_index)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_api_access_tokens_revoked ON auth_api_access_tokens(revoked)"
        )
    except Exception:
        pass


def down(db):
    """Rollback the migration.

    Drops the auth_api_access_tokens table.
    """
    logger.info("Migration 014 rollback: Starting rollback")
    if db.table_exists("auth_api_access_tokens"):
        db.execute("DROP TABLE IF EXISTS auth_api_access_tokens")
        logger.info("Migration 014 rollback: Dropped auth_api_access_tokens table")
