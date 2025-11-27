"""
Voice database schema - Table definitions for voice module.
"""

import utils.logger as logger


SCHEMA = """
-- Voice states table (users currently in voice channels)
CREATE TABLE IF NOT EXISTS voice_states (
    user_id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    server_id INTEGER NOT NULL,
    self_mute INTEGER DEFAULT 0,
    self_deaf INTEGER DEFAULT 0,
    server_mute INTEGER DEFAULT 0,
    server_deaf INTEGER DEFAULT 0,
    suppress INTEGER DEFAULT 0,
    streaming INTEGER DEFAULT 0,
    video INTEGER DEFAULT 0,
    joined_at INTEGER NOT NULL,
    last_activity INTEGER NOT NULL
);

-- Voice state indexes
CREATE INDEX IF NOT EXISTS idx_voice_states_channel ON voice_states(channel_id);
CREATE INDEX IF NOT EXISTS idx_voice_states_server ON voice_states(server_id);

-- Voice channel settings table (extends srv_channels)
CREATE TABLE IF NOT EXISTS voice_channel_settings (
    channel_id INTEGER PRIMARY KEY,
    user_limit INTEGER DEFAULT 0,
    bitrate INTEGER DEFAULT 64000,
    region_id TEXT
);

-- Stage instances table (active stages)
CREATE TABLE IF NOT EXISTS voice_stage_instances (
    id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL UNIQUE,
    server_id INTEGER NOT NULL,
    topic TEXT NOT NULL,
    started_by INTEGER NOT NULL,
    started_at INTEGER NOT NULL
);

-- Stage instance indexes
CREATE INDEX IF NOT EXISTS idx_voice_stage_channel ON voice_stage_instances(channel_id);
CREATE INDEX IF NOT EXISTS idx_voice_stage_server ON voice_stage_instances(server_id);

-- Speaker requests table (raise hand)
CREATE TABLE IF NOT EXISTS voice_speaker_requests (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    requested_at INTEGER NOT NULL,
    UNIQUE(user_id, channel_id)
);

-- Speaker request indexes
CREATE INDEX IF NOT EXISTS idx_voice_speaker_channel ON voice_speaker_requests(channel_id);

-- AFK settings table
CREATE TABLE IF NOT EXISTS voice_afk_settings (
    server_id INTEGER PRIMARY KEY,
    channel_id INTEGER,
    timeout_seconds INTEGER DEFAULT 300
);
"""


def create_tables(db):
    """Create all voice tables."""
    statements = [s.strip() for s in SCHEMA.split(";") if s.strip()]
    
    for statement in statements:
        if statement:
            try:
                db.execute(statement)
            except Exception as e:
                logger.error(f"Failed to execute schema statement: {e}")
                raise
    
    logger.info("Voice tables created successfully")
