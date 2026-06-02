"""
Avatars module schema.

This module owns the canonical DDL for the avatars module. It is safe to call
``create_tables(db)`` before :func:`src.core.avatars.setup` has been invoked,
which is required because migration 000 runs at startup prior to module setup.
"""

from typing import Any

SCHEMA_SQLITE = """
-- User avatars
CREATE TABLE IF NOT EXISTS user_avatars (
    id BIGINT PRIMARY KEY,
    user_id BIGINT NOT NULL UNIQUE,
    avatar_data BLOB NOT NULL,
    content_type TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    size INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    animated INTEGER NOT NULL DEFAULT 0,
    uploaded_at BIGINT NOT NULL
);

-- Server icons (no FK to servers; servers table may not exist yet)
CREATE TABLE IF NOT EXISTS server_icons (
    id BIGINT PRIMARY KEY,
    server_id BIGINT NOT NULL UNIQUE,
    icon_data BLOB NOT NULL,
    content_type TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    size INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    animated INTEGER NOT NULL DEFAULT 0,
    uploaded_at BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_avatars_user ON user_avatars(user_id);
CREATE INDEX IF NOT EXISTS idx_server_icons_server ON server_icons(server_id);
"""


def create_tables(db: Any) -> None:
    """Create avatar tables. Safe to call before module setup."""
    from src.core.database.core.schema_splitter import split_sql_statements

    statements = split_sql_statements(SCHEMA_SQLITE)
    for statement in statements:
        converted = (
            db.convert_schema(statement) if hasattr(db, "convert_schema") else statement
        )
        db.execute(converted)
