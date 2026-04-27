"""
Expand API access tokens with additional fields.
"""

import logging

logger = logging.getLogger(__name__)


def _add_column_if_missing(db, table: str, column: str, ddl: str) -> None:
    try:
        if db.column_exists(table, column):
            return
    except Exception:
        pass
    try:
        # Sanitize table name to prevent SQL injection in ALTER TABLE
        from src.core.database import dialect

        safe_table = dialect.sanitize_identifier(table, db.type)
        db.execute(f"ALTER TABLE {safe_table} ADD COLUMN {ddl}")
    except Exception:
        pass


def up(db):
    try:
        _add_column_if_missing(
            db, "auth_api_access_tokens", "description", "description TEXT"
        )
        _add_column_if_missing(
            db, "auth_api_access_tokens", "first_used_at", "first_used_at BIGINT"
        )
        _add_column_if_missing(
            db,
            "auth_api_access_tokens",
            "last_used_ip_index",
            "last_used_ip_index TEXT",
        )
        _add_column_if_missing(
            db,
            "auth_api_access_tokens",
            "last_used_ip_encrypted",
            "last_used_ip_encrypted TEXT",
        )
        _add_column_if_missing(
            db,
            "auth_api_access_tokens",
            "last_used_user_agent",
            "last_used_user_agent TEXT",
        )
        _add_column_if_missing(
            db, "auth_api_access_tokens", "last_used_path", "last_used_path TEXT"
        )
        _add_column_if_missing(
            db, "auth_api_access_tokens", "expires_at", "expires_at BIGINT"
        )
        _add_column_if_missing(
            db,
            "auth_api_access_tokens",
            "scope_mode",
            "scope_mode TEXT NOT NULL DEFAULT 'none'",
        )
        _add_column_if_missing(
            db,
            "auth_api_access_tokens",
            "use_count_total",
            "use_count_total INTEGER NOT NULL DEFAULT 0",
        )

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_api_access_token_scopes (
                id BIGINT PRIMARY KEY,
                token_id BIGINT NOT NULL,
                scope_type TEXT NOT NULL,
                value TEXT NOT NULL,
                created_by BIGINT,
                created_at BIGINT NOT NULL,
                UNIQUE(token_id, scope_type, value)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_api_access_token_events (
                id BIGINT PRIMARY KEY,
                token_id BIGINT NOT NULL,
                used_at BIGINT NOT NULL,
                ip_index TEXT,
                ip_encrypted TEXT,
                method TEXT,
                path TEXT,
                user_agent TEXT,
                allowed INTEGER NOT NULL DEFAULT 1,
                scope_match INTEGER,
                reject_reason TEXT
            )
            """
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_api_access_tokens_expires ON auth_api_access_tokens(expires_at)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_api_access_token_scopes_token ON auth_api_access_token_scopes(token_id)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_api_access_token_events_token_time ON auth_api_access_token_events(token_id, used_at DESC)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_api_access_token_events_ip ON auth_api_access_token_events(ip_index)"
        )
        logger.info("Migration 016: API access tokens expansion complete")
    except Exception:
        logger.exception("Migration 016: API access tokens expansion failed")


def down(db):
    """Rollback the migration.

    Drops the new tables and clears added columns.
    For PostgreSQL: Drops columns as well.
    For SQLite: Columns left in place (DROP COLUMN not supported).
    """
    logger.info("Migration 016 rollback: Starting rollback")
    # Drop new tables
    if db.table_exists("auth_api_access_token_events"):
        db.execute("DROP TABLE IF EXISTS auth_api_access_token_events")
    if db.table_exists("auth_api_access_token_scopes"):
        db.execute("DROP TABLE IF EXISTS auth_api_access_token_scopes")

    # For PostgreSQL, drop the added columns
    if db.type == "postgres":
        columns_to_drop = [
            "description",
            "first_used_at",
            "last_used_ip_index",
            "last_used_ip_encrypted",
            "last_used_user_agent",
            "last_used_path",
            "expires_at",
            "scope_mode",
            "use_count_total",
        ]
        for col in columns_to_drop:
            if db.column_exists("auth_api_access_tokens", col):
                db.execute(f"ALTER TABLE auth_api_access_tokens DROP COLUMN {col}")
        logger.info("Migration 016 rollback: Dropped tables and columns (PostgreSQL)")
    else:
        # SQLite: Clear column values but leave columns
        columns_to_clear = [
            "description",
            "first_used_at",
            "last_used_ip_index",
            "last_used_ip_encrypted",
            "last_used_user_agent",
            "last_used_path",
            "expires_at",
            "scope_mode",
            "use_count_total",
        ]
        for col in columns_to_clear:
            if db.column_exists("auth_api_access_tokens", col):
                db.execute(f"UPDATE auth_api_access_tokens SET {col} = NULL")
        logger.info(
            "Migration 016 rollback: Dropped tables, cleared column values (SQLite - columns left in place)"
        )
