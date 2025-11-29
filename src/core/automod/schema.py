"""
AutoMod database schema - Table definitions for automod module.
"""

import utils.logger as logger


SCHEMA = """
-- Server automod configuration
CREATE TABLE IF NOT EXISTS automod_config (
    server_id INTEGER PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    log_channel_id INTEGER,
    alert_channel_id INTEGER,
    alert_webhook_url TEXT,
    default_timeout_duration INTEGER NOT NULL DEFAULT 300,
    reputation_enabled INTEGER NOT NULL DEFAULT 1,
    reputation_decay_rate REAL NOT NULL DEFAULT 1.0,
    reputation_decay_interval_hours INTEGER NOT NULL DEFAULT 24,
    ai_backend TEXT,
    ai_enabled INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- Automod rules
CREATE TABLE IF NOT EXISTS automod_rules (
    id INTEGER PRIMARY KEY,
    server_id INTEGER NOT NULL,
    rule_type TEXT NOT NULL,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL DEFAULT 0,
    trigger_config TEXT NOT NULL,
    actions TEXT NOT NULL,
    exempt_roles TEXT,
    exempt_channels TEXT,
    cooldown_seconds INTEGER NOT NULL DEFAULT 0,
    check_all_rules INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    created_by INTEGER NOT NULL,
    FOREIGN KEY (server_id) REFERENCES srv_servers(id) ON DELETE CASCADE
);

-- Rule violations
CREATE TABLE IF NOT EXISTS automod_violations (
    id INTEGER PRIMARY KEY,
    server_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    message_id INTEGER,
    rule_id INTEGER NOT NULL,
    rule_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'medium',
    matched_content TEXT,
    trigger_details TEXT,
    actions_taken TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (server_id) REFERENCES srv_servers(id) ON DELETE CASCADE,
    FOREIGN KEY (rule_id) REFERENCES automod_rules(id) ON DELETE CASCADE
);

-- Audit log for automod actions
CREATE TABLE IF NOT EXISTS automod_audit (
    id INTEGER PRIMARY KEY,
    server_id INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    target_user_id INTEGER,
    rule_id INTEGER,
    violation_id INTEGER,
    message_id INTEGER,
    channel_id INTEGER,
    reason TEXT,
    metadata TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (server_id) REFERENCES srv_servers(id) ON DELETE CASCADE
);

-- User reputation per server
CREATE TABLE IF NOT EXISTS automod_reputation (
    user_id INTEGER NOT NULL,
    server_id INTEGER NOT NULL,
    score REAL NOT NULL DEFAULT 100.0,
    total_violations INTEGER NOT NULL DEFAULT 0,
    last_violation_at INTEGER,
    last_decay_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, server_id),
    FOREIGN KEY (server_id) REFERENCES srv_servers(id) ON DELETE CASCADE
);

-- User cooldowns for rate limiting
CREATE TABLE IF NOT EXISTS automod_cooldowns (
    user_id INTEGER NOT NULL,
    server_id INTEGER NOT NULL,
    rule_id INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, server_id, rule_id)
);

-- Message history for spam detection
CREATE TABLE IF NOT EXISTS automod_message_history (
    id INTEGER PRIMARY KEY,
    server_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_automod_rules_server ON automod_rules(server_id);
CREATE INDEX IF NOT EXISTS idx_automod_rules_type ON automod_rules(rule_type);
CREATE INDEX IF NOT EXISTS idx_automod_violations_server ON automod_violations(server_id);
CREATE INDEX IF NOT EXISTS idx_automod_violations_user ON automod_violations(user_id);
CREATE INDEX IF NOT EXISTS idx_automod_violations_rule ON automod_violations(rule_id);
CREATE INDEX IF NOT EXISTS idx_automod_violations_created ON automod_violations(created_at);
CREATE INDEX IF NOT EXISTS idx_automod_audit_server ON automod_audit(server_id);
CREATE INDEX IF NOT EXISTS idx_automod_audit_user ON automod_audit(user_id);
CREATE INDEX IF NOT EXISTS idx_automod_audit_created ON automod_audit(created_at);
CREATE INDEX IF NOT EXISTS idx_automod_reputation_server ON automod_reputation(server_id);
CREATE INDEX IF NOT EXISTS idx_automod_cooldowns_expires ON automod_cooldowns(expires_at);
CREATE INDEX IF NOT EXISTS idx_automod_msg_history_user ON automod_message_history(user_id, server_id);
CREATE INDEX IF NOT EXISTS idx_automod_msg_history_created ON automod_message_history(created_at);
"""


def create_tables(db):
    """Create all automod tables."""
    statements = [s.strip() for s in SCHEMA.split(";") if s.strip()]
    
    for statement in statements:
        if statement:
            try:
                db.execute(statement)
            except Exception as e:
                logger.error(f"Failed to execute schema statement: {e}")
                raise
    
    logger.info("AutoMod tables created successfully")
