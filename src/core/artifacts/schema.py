"""
Artifacts database schema - Table definitions for the artifacts module.

All IDs use Snowflake format (stored as INTEGER) for distributed generation,
matching the convention used by the voice and messaging modules.
"""

import utils.logger as logger

from src.core.database.core.schema_splitter import split_sql_statements


SCHEMA = """
-- Artifacts table (first-class persistent records for calls, whiteboards, etc.)
CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER,
    channel_id INTEGER,
    server_id INTEGER,
    author_id INTEGER NOT NULL,
    artifact_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    status TEXT NOT NULL DEFAULT 'completed',
    recorded INTEGER NOT NULL DEFAULT 0,
    has_transcript INTEGER NOT NULL DEFAULT 0,
    payload TEXT,
    retention_policy TEXT,
    expires_at INTEGER,
    license_feature TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- Artifacts indexes
CREATE INDEX IF NOT EXISTS idx_artifacts_conversation ON artifacts(conversation_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_server ON artifacts(server_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_author ON artifacts(author_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_type ON artifacts(artifact_type);
CREATE INDEX IF NOT EXISTS idx_artifacts_created ON artifacts(created_at);

-- Voice calls table (call-specific metadata; may reference an artifact)
CREATE TABLE IF NOT EXISTS voice_calls (
    id INTEGER PRIMARY KEY,
    artifact_id INTEGER,
    conversation_id INTEGER,
    channel_id INTEGER,
    server_id INTEGER,
    initiator_id INTEGER,
    started_at INTEGER NOT NULL,
    ended_at INTEGER,
    duration_seconds INTEGER,
    recorded INTEGER NOT NULL DEFAULT 0,
    transcript_artifact_id INTEGER,
    consented_participants TEXT,
    participant_count INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- Voice calls indexes
CREATE INDEX IF NOT EXISTS idx_voice_calls_artifact ON voice_calls(artifact_id);
CREATE INDEX IF NOT EXISTS idx_voice_calls_conversation ON voice_calls(conversation_id);
CREATE INDEX IF NOT EXISTS idx_voice_calls_server ON voice_calls(server_id);

-- Artifact ops table (ordered operations log for collaborative artifacts)
CREATE TABLE IF NOT EXISTS artifact_ops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    op_type TEXT NOT NULL,
    actor_id INTEGER,
    data TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(artifact_id, seq)
);

-- Artifact ops indexes
CREATE INDEX IF NOT EXISTS idx_artifact_ops_artifact ON artifact_ops(artifact_id);
CREATE INDEX IF NOT EXISTS idx_artifact_ops_artifact_seq ON artifact_ops(artifact_id, seq);
"""


def create_tables(db):
    """Create all artifacts tables."""
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

    logger.info("Artifacts tables created successfully")
