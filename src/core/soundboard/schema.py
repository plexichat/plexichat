"""
Soundboard database schema - Table definitions for soundboard module.
"""

import utils.logger as logger

from src.core.database.core.schema_splitter import split_sql_statements


SCHEMA = """
-- Soundboard sounds table
CREATE TABLE IF NOT EXISTS soundboard_sounds (
    id INTEGER PRIMARY KEY,
    server_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    format TEXT NOT NULL DEFAULT 'mp3',
    emoji TEXT,
    url TEXT NOT NULL,
    size INTEGER NOT NULL,
    duration_seconds REAL NOT NULL,
    volume REAL NOT NULL DEFAULT 1.0,
    -- ``cooldown_seconds`` is NULL when the sound was just uploaded and
    -- no explicit per-sound cooldown was set; the manager treats NULL as
    -- "fall back to ``default_cooldown_seconds``" in the active config.
    -- Storing ``0`` (not NULL) as the default would be ambiguous between
    -- "explicit zero cooldown" and "unset, use default"; using NULL
    -- distinguishes the two cases cleanly.
    cooldown_seconds INTEGER,
    created_by INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

-- Soundboard sounds indexes
CREATE INDEX IF NOT EXISTS idx_soundboard_sounds_server ON soundboard_sounds(server_id);
CREATE INDEX IF NOT EXISTS idx_soundboard_sounds_name ON soundboard_sounds(name);
CREATE INDEX IF NOT EXISTS idx_soundboard_sounds_creator ON soundboard_sounds(created_by);

-- Sound permissions table (role-based)
CREATE TABLE IF NOT EXISTS soundboard_permissions (
    id INTEGER PRIMARY KEY,
    sound_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    can_use INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (sound_id) REFERENCES soundboard_sounds(id) ON DELETE CASCADE,
    UNIQUE(sound_id, role_id)
);

-- Sound permissions indexes
CREATE INDEX IF NOT EXISTS idx_soundboard_permissions_sound ON soundboard_permissions(sound_id);
CREATE INDEX IF NOT EXISTS idx_soundboard_permissions_role ON soundboard_permissions(role_id);

-- Sound usage tracking table
CREATE TABLE IF NOT EXISTS soundboard_usage (
    id INTEGER PRIMARY KEY,
    sound_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    used_at INTEGER NOT NULL,
    FOREIGN KEY (sound_id) REFERENCES soundboard_sounds(id) ON DELETE CASCADE
);

-- Sound usage indexes
CREATE INDEX IF NOT EXISTS idx_soundboard_usage_sound ON soundboard_usage(sound_id);
CREATE INDEX IF NOT EXISTS idx_soundboard_usage_user ON soundboard_usage(user_id);
CREATE INDEX IF NOT EXISTS idx_soundboard_usage_channel ON soundboard_usage(channel_id);

-- Per-user last-play tracker for cooldown enforcement.
-- Replaces the in-memory `self._cooldowns` dict so cooldowns survive
-- process restarts and multi-instance deployments.
CREATE TABLE IF NOT EXISTS soundboard_user_cooldowns (
    user_id INTEGER NOT NULL,
    sound_id INTEGER NOT NULL,
    server_id INTEGER NOT NULL,
    last_play_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, sound_id),
    FOREIGN KEY (sound_id) REFERENCES soundboard_sounds(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_soundboard_user_cooldowns_user
    ON soundboard_user_cooldowns(user_id);
CREATE INDEX IF NOT EXISTS idx_soundboard_user_cooldowns_server_user
    ON soundboard_user_cooldowns(server_id, user_id, last_play_at);
"""


def create_tables(db):
    """Create all soundboard tables."""
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

    logger.info("Soundboard tables created successfully")
