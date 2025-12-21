"""
Soundboard database schema - Table definitions for soundboard module.
"""

import utils.logger as logger


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
"""


def create_tables(db):
    """Create all soundboard tables."""
    statements = [s.strip() for s in SCHEMA.split(";") if s.strip()]

    for statement in statements:
        if statement:
            try:
                db.execute(statement)
            except Exception as e:
                logger.error(f"Failed to execute schema statement: {e}")
                raise

    logger.info("Soundboard tables created successfully")
