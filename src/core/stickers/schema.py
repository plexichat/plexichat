"""
Sticker database schema - Table definitions for stickers module.
"""

import utils.logger as logger

from src.core.database.core.schema_splitter import split_sql_statements


SCHEMA = """
-- Sticker packs table
CREATE TABLE IF NOT EXISTS sticker_packs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description_encrypted TEXT,
    pack_type TEXT NOT NULL DEFAULT 'server',
    server_id INTEGER,
    created_by INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    is_public INTEGER NOT NULL DEFAULT 0
);

-- Sticker packs indexes
CREATE INDEX IF NOT EXISTS idx_sticker_packs_server ON sticker_packs(server_id);
CREATE INDEX IF NOT EXISTS idx_sticker_packs_type ON sticker_packs(pack_type);
CREATE INDEX IF NOT EXISTS idx_sticker_packs_creator ON sticker_packs(created_by);

-- Stickers table
CREATE TABLE IF NOT EXISTS sticker_stickers (
    id INTEGER PRIMARY KEY,
    pack_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    format TEXT NOT NULL DEFAULT 'png',
    description TEXT,
    tags TEXT,
    related_emoji TEXT,
    url TEXT NOT NULL,
    size INTEGER NOT NULL,
    width INTEGER,
    height INTEGER,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (pack_id) REFERENCES sticker_packs(id) ON DELETE CASCADE
);

-- Stickers indexes
CREATE INDEX IF NOT EXISTS idx_sticker_stickers_pack ON sticker_stickers(pack_id);
CREATE INDEX IF NOT EXISTS idx_sticker_stickers_name ON sticker_stickers(name);
CREATE INDEX IF NOT EXISTS idx_sticker_stickers_tags ON sticker_stickers(tags);

-- Sticker usage tracking table
CREATE TABLE IF NOT EXISTS sticker_usage (
    id INTEGER PRIMARY KEY,
    sticker_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    used_at INTEGER NOT NULL,
    FOREIGN KEY (sticker_id) REFERENCES sticker_stickers(id) ON DELETE CASCADE
);

-- Sticker usage indexes
CREATE INDEX IF NOT EXISTS idx_sticker_usage_sticker ON sticker_usage(sticker_id);
CREATE INDEX IF NOT EXISTS idx_sticker_usage_user ON sticker_usage(user_id);
CREATE INDEX IF NOT EXISTS idx_sticker_usage_message ON sticker_usage(message_id);
"""


def create_tables(db):
    """Create all sticker tables."""
    statements = split_sql_statements(SCHEMA)

    for statement in statements:
        if statement:
            try:
                converted = (
                    db.convert_schema(statement)
                    if hasattr(db, "convert_schema")
                    else statement
                )
                db.execute(converted)
            except Exception as e:
                logger.error(f"Failed to execute schema statement: {e}")
                raise

    logger.info("Sticker tables created successfully")
