"""
Reports database schema - Table definitions for reports module.

Defines three tables:
- message_reports: Per-message content reports
- user_reports: Per-user behaviour reports
- reports: Unified reports table used by the enhanced reports feature
"""

from src.core.database.core.schema_splitter import split_sql_statements

import utils.logger as logger


SCHEMA = """
-- Unified reports table (used by enhanced reports feature)
CREATE TABLE IF NOT EXISTS reports (
    id BIGINT PRIMARY KEY,
    reporter_id BIGINT NOT NULL,
    report_type TEXT NOT NULL,
    target_id BIGINT NOT NULL,
    channel_id BIGINT,
    server_id BIGINT,
    reason TEXT NOT NULL,
    category TEXT,
    details TEXT,
    evidence_ids TEXT,
    message_content TEXT,
    reported_user_id BIGINT,
    status TEXT NOT NULL DEFAULT 'open',
    priority TEXT NOT NULL DEFAULT 'medium',
    assigned_to BIGINT,
    admin_notes TEXT,
    resolution TEXT,
    resolved_at BIGINT,
    resolved_by BIGINT,
    reviewed_at BIGINT,
    reviewed_by BIGINT,
    escalated_at BIGINT,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL
);

-- Unified reports indexes
CREATE INDEX IF NOT EXISTS idx_reports_reporter ON reports(reporter_id);
-- Reporter history lists a reporter's reports newest-first.
CREATE INDEX IF NOT EXISTS idx_reports_reporter_created ON reports(reporter_id, created_at);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);
CREATE INDEX IF NOT EXISTS idx_reports_target ON reports(target_id);
-- Target history lists reports against a user newest-first.
CREATE INDEX IF NOT EXISTS idx_reports_target_created ON reports(target_id, created_at);
CREATE INDEX IF NOT EXISTS idx_reports_server ON reports(server_id);
-- Server-scoped report lists order by created_at.
CREATE INDEX IF NOT EXISTS idx_reports_server_created ON reports(server_id, created_at);
CREATE INDEX IF NOT EXISTS idx_reports_assigned ON reports(assigned_to);
-- Assigned-queue filters assigned_to + status.
CREATE INDEX IF NOT EXISTS idx_reports_assigned_status ON reports(assigned_to, status);
CREATE INDEX IF NOT EXISTS idx_reports_priority ON reports(priority);
-- Priority queues order by priority then recency.
CREATE INDEX IF NOT EXISTS idx_reports_priority_created ON reports(priority, created_at);
-- The moderator queue filters on status and orders by created_at DESC. A
-- composite index lets that query read in order instead of sorting every row.
CREATE INDEX IF NOT EXISTS idx_reports_status_created ON reports(status, created_at);
CREATE INDEX IF NOT EXISTS idx_reports_created ON reports(created_at);

-- A reporter may have only one active report for a target. This partial
-- index is supported by SQLite and PostgreSQL and closes concurrent duplicate
-- inserts at the database boundary.
CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_active_reporter_target
ON reports(reporter_id, report_type, target_id)
WHERE status IN ('open', 'investigating');


-- Message reports
CREATE TABLE IF NOT EXISTS message_reports (
    id BIGINT PRIMARY KEY,
    message_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    server_id BIGINT,
    reporter_id BIGINT NOT NULL,
    reported_user_id BIGINT NOT NULL,
    reason TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'other',
    details TEXT,
    message_content TEXT,
    evidence_urls TEXT,
    priority TEXT NOT NULL DEFAULT 'medium',
    status TEXT NOT NULL DEFAULT 'pending',
    reported_at BIGINT NOT NULL,
    reviewed_at BIGINT,
    reviewed_by BIGINT,
    admin_notes TEXT,
    action_taken TEXT,
    assigned_to BIGINT,
    escalated_at BIGINT,
    resolution TEXT
);

-- Message reports indexes
CREATE INDEX IF NOT EXISTS idx_message_reports_status ON message_reports(status);
CREATE INDEX IF NOT EXISTS idx_message_reports_reporter ON message_reports(reporter_id);
CREATE INDEX IF NOT EXISTS idx_message_reports_reporter_at
    ON message_reports(reporter_id, reported_at);
CREATE INDEX IF NOT EXISTS idx_message_reports_reported ON message_reports(reported_user_id);
CREATE INDEX IF NOT EXISTS idx_message_reports_message ON message_reports(message_id);
CREATE INDEX IF NOT EXISTS idx_message_reports_priority ON message_reports(priority);
CREATE INDEX IF NOT EXISTS idx_message_reports_assigned ON message_reports(assigned_to);
-- Moderation queues order by reported_at.
CREATE INDEX IF NOT EXISTS idx_message_reports_reported_at ON message_reports(reported_at);

-- User reports
CREATE TABLE IF NOT EXISTS user_reports (
    id BIGINT PRIMARY KEY,
    reported_user_id BIGINT NOT NULL,
    reporter_id BIGINT NOT NULL,
    reason TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'other',
    details TEXT,
    evidence_message_ids TEXT,
    evidence_urls TEXT,
    priority TEXT NOT NULL DEFAULT 'medium',
    status TEXT NOT NULL DEFAULT 'pending',
    reported_at BIGINT NOT NULL,
    reviewed_at BIGINT,
    reviewed_by BIGINT,
    admin_notes TEXT,
    action_taken TEXT,
    assigned_to BIGINT,
    escalated_at BIGINT,
    resolution TEXT
);

-- User reports indexes
CREATE INDEX IF NOT EXISTS idx_user_reports_status ON user_reports(status);
CREATE INDEX IF NOT EXISTS idx_user_reports_reporter ON user_reports(reporter_id);
CREATE INDEX IF NOT EXISTS idx_user_reports_reported ON user_reports(reported_user_id);
CREATE INDEX IF NOT EXISTS idx_user_reports_priority ON user_reports(priority);
CREATE INDEX IF NOT EXISTS idx_user_reports_assigned ON user_reports(assigned_to);
CREATE INDEX IF NOT EXISTS idx_user_reports_reported_at ON user_reports(reported_at);
"""


def create_tables(db) -> None:
    """Create all report tables."""
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
                logger.error(f"Failed to create reports table: {e}")
                raise

    logger.info("Report tables created successfully")
