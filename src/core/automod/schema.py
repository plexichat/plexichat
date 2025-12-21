"""
Auto-moderation database schema.

Creates tables for rules, violations, audit log, and reputation.
All tables prefixed with 'automod_'.
"""


def create_tables(db):
    """Create all automod tables."""

    db.execute("""
        CREATE TABLE IF NOT EXISTS automod_rules (
            id INTEGER PRIMARY KEY,
            server_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            rule_type TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            config TEXT NOT NULL,
            actions TEXT NOT NULL,
            exempt_roles TEXT DEFAULT '[]',
            exempt_channels TEXT DEFAULT '[]',
            priority INTEGER DEFAULT 0,
            check_all INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            created_by INTEGER NOT NULL
        )
    """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_automod_rules_server 
        ON automod_rules(server_id, enabled)
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS automod_violations (
            id INTEGER PRIMARY KEY,
            server_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            message_id INTEGER,
            rule_id INTEGER NOT NULL,
            rule_type TEXT NOT NULL,
            matched_content TEXT NOT NULL,
            actions_taken TEXT NOT NULL,
            severity TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            created_at INTEGER NOT NULL
        )
    """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_automod_violations_server 
        ON automod_violations(server_id, created_at DESC)
    """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_automod_violations_user 
        ON automod_violations(user_id, server_id)
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS automod_audit (
            id INTEGER PRIMARY KEY,
            server_id INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            target_user_id INTEGER NOT NULL,
            moderator_id INTEGER,
            rule_id INTEGER,
            reason TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            created_at INTEGER NOT NULL
        )
    """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_automod_audit_server 
        ON automod_audit(server_id, created_at DESC)
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS automod_reputation (
            user_id INTEGER NOT NULL,
            server_id INTEGER NOT NULL,
            score REAL DEFAULT 100.0,
            violation_count INTEGER DEFAULT 0,
            last_violation_at INTEGER,
            last_decay_at INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (user_id, server_id)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS automod_exemptions (
            id INTEGER PRIMARY KEY,
            server_id INTEGER NOT NULL,
            rule_id INTEGER,
            target_type TEXT NOT NULL,
            target_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            created_by INTEGER NOT NULL
        )
    """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_automod_exemptions_server 
        ON automod_exemptions(server_id, rule_id)
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS automod_rate_tracking (
            id INTEGER PRIMARY KEY,
            server_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            rule_type TEXT NOT NULL,
            window_start INTEGER NOT NULL,
            count INTEGER DEFAULT 1,
            UNIQUE(server_id, user_id, rule_type, window_start)
        )
    """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_automod_rate_tracking_lookup 
        ON automod_rate_tracking(server_id, user_id, rule_type, window_start)
    """)
