"""
Embed database schema - Table definitions for embeds module.
"""

import utils.logger as logger


SCHEMA = """
-- Embeds table
CREATE TABLE IF NOT EXISTS embed_embeds (
    id INTEGER PRIMARY KEY,
    embed_type TEXT NOT NULL DEFAULT 'rich',
    title TEXT,
    description TEXT,
    url TEXT,
    timestamp TEXT,
    color TEXT,
    footer_text TEXT,
    footer_icon_url TEXT,
    image_url TEXT,
    image_width INTEGER,
    image_height INTEGER,
    thumbnail_url TEXT,
    thumbnail_width INTEGER,
    thumbnail_height INTEGER,
    author_name TEXT,
    author_url TEXT,
    author_icon_url TEXT,
    provider_name TEXT,
    provider_url TEXT,
    created_by INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    is_url_preview INTEGER NOT NULL DEFAULT 0,
    source_url TEXT
);

-- Embed fields table
CREATE TABLE IF NOT EXISTS embed_fields (
    id INTEGER PRIMARY KEY,
    embed_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    value TEXT NOT NULL,
    inline INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (embed_id) REFERENCES embed_embeds(id) ON DELETE CASCADE
);

-- Message embeds association table
CREATE TABLE IF NOT EXISTS embed_message_embeds (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL,
    embed_id INTEGER NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    suppressed INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    UNIQUE(message_id, embed_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_embed_created_by ON embed_embeds(created_by);
CREATE INDEX IF NOT EXISTS idx_embed_fields_embed ON embed_fields(embed_id);
CREATE INDEX IF NOT EXISTS idx_embed_message ON embed_message_embeds(message_id);
CREATE INDEX IF NOT EXISTS idx_embed_embed ON embed_message_embeds(embed_id);
"""


def create_tables(db):
    """Create all embed tables."""
    statements = [s.strip() for s in SCHEMA.split(";") if s.strip()]

    for statement in statements:
        if statement:
            try:
                converted = db.convert_schema(statement) if hasattr(db, 'convert_schema') else statement
                db.execute(converted)
            except Exception as e:
                logger.error(f"Failed to execute schema statement: {e}")
                raise

    logger.info("Embed tables created successfully")
