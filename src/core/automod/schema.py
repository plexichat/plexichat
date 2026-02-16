"""
Auto-moderation database schema.

Creates tables for rules, violations, audit log, and reputation.
All tables prefixed with 'automod_'.
"""


def create_tables(db):
    """Create all automod tables."""

    db.execute("""
        CREATE TABLE IF NOT EXISTS automod_rules (
            id BIGINT PRIMARY KEY,
            server_id BIGINT NOT NULL,
            name TEXT NOT NULL,
            rule_type TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            config TEXT NOT NULL,
            actions TEXT NOT NULL,
            exempt_roles TEXT DEFAULT '[]',
            exempt_channels TEXT DEFAULT '[]',
            priority INTEGER DEFAULT 0,
            check_all INTEGER DEFAULT 0,
            created_at BIGINT NOT NULL,
            updated_at BIGINT NOT NULL,
            created_by BIGINT NOT NULL
        )
    """)

    # Migrations for existing tables (PostgreSQL only)
    if db.type == "postgres":
        tables_to_fix = [
            ("automod_rules", ["id", "server_id", "created_at", "updated_at", "created_by"]),
            ("automod_violations", ["id", "server_id", "channel_id", "user_id", "message_id", "rule_id", "created_at"]),
            ("automod_audit", ["id", "server_id", "target_user_id", "moderator_id", "rule_id", "created_at"]),
            ("automod_reputation", ["user_id", "server_id", "last_violation_at", "last_decay_at", "created_at", "updated_at"]),
            ("automod_exemptions", ["id", "server_id", "rule_id", "target_id", "created_at", "created_by"]),
            ("automod_rate_tracking", ["id", "server_id", "user_id", "window_start"])
        ]
        
        for table, columns in tables_to_fix:
            for col in columns:
                try:
                    db.execute(f"ALTER TABLE {table} ALTER COLUMN {col} TYPE BIGINT")
                except Exception:
                    pass

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_automod_rules_server 
        ON automod_rules(server_id, enabled)
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS automod_violations (
            id BIGINT PRIMARY KEY,
            server_id BIGINT NOT NULL,
            channel_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            message_id BIGINT,
            rule_id BIGINT NOT NULL,
            rule_type TEXT NOT NULL,
            matched_content TEXT NOT NULL,
            actions_taken TEXT NOT NULL,
            severity TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            created_at BIGINT NOT NULL
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
            id BIGINT PRIMARY KEY,
            server_id BIGINT NOT NULL,
            action_type TEXT NOT NULL,
            target_user_id BIGINT NOT NULL,
            moderator_id BIGINT,
            rule_id BIGINT,
            reason TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            created_at BIGINT NOT NULL
        )
    """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_automod_audit_server 
        ON automod_audit(server_id, created_at DESC)
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS automod_reputation (
            user_id BIGINT NOT NULL,
            server_id BIGINT NOT NULL,
            score REAL DEFAULT 100.0,
            violation_count INTEGER DEFAULT 0,
            last_violation_at BIGINT,
            last_decay_at BIGINT NOT NULL,
            created_at BIGINT NOT NULL,
            updated_at BIGINT NOT NULL,
            PRIMARY KEY (user_id, server_id)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS automod_exemptions (
            id BIGINT PRIMARY KEY,
            server_id BIGINT NOT NULL,
            rule_id BIGINT,
            target_type TEXT NOT NULL,
            target_id BIGINT NOT NULL,
            created_at BIGINT NOT NULL,
            created_by BIGINT NOT NULL
        )
    """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_automod_exemptions_server 
        ON automod_exemptions(server_id, rule_id)
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS automod_rate_tracking (
            id BIGINT PRIMARY KEY,
            server_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            rule_type TEXT NOT NULL,
            window_start BIGINT NOT NULL,
            count INTEGER DEFAULT 1,
            UNIQUE(server_id, user_id, rule_type, window_start)
        )
    """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_automod_rate_tracking_lookup 
        ON automod_rate_tracking(server_id, user_id, rule_type, window_start)
    """)
