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
            applied_roles TEXT DEFAULT '[]',
            exempt_roles TEXT DEFAULT '[]',
            exempt_channels TEXT DEFAULT '[]',
            priority INTEGER DEFAULT 0,
            check_all INTEGER DEFAULT 0,
            -- Encrypted reason for automod bans (commit aaf523b7)
            reason_encrypted TEXT,
            created_at BIGINT NOT NULL,
            updated_at BIGINT NOT NULL,
            created_by BIGINT NOT NULL
        )
    """)

    # Migrations for existing tables (PostgreSQL only)
    # Fix columns that were created as TEXT or INTEGER instead of BIGINT.
    # Uses USING clause for safe type conversion — if the column already contains
    # non-numeric data (e.g. a column name leak), the USING clause will convert
    # valid integers and set invalid values to NULL rather than failing.
    if db.type == "postgres":
        # Validate table/column identifiers to prevent SQL injection in DDL
        allowed_tables = {
            "automod_rules",
            "automod_violations",
            "automod_audit",
            "automod_reputation",
            "automod_exemptions",
            "automod_rate_tracking",
        }
        tables_to_fix = [
            (
                "automod_rules",
                ["id", "server_id", "created_at", "updated_at", "created_by"],
            ),
            (
                "automod_violations",
                [
                    "id",
                    "server_id",
                    "channel_id",
                    "user_id",
                    "message_id",
                    "rule_id",
                    "created_at",
                ],
            ),
            (
                "automod_audit",
                [
                    "id",
                    "server_id",
                    "target_user_id",
                    "moderator_id",
                    "rule_id",
                    "created_at",
                ],
            ),
            (
                "automod_reputation",
                [
                    "user_id",
                    "server_id",
                    "last_violation_at",
                    "last_decay_at",
                    "created_at",
                    "updated_at",
                ],
            ),
            (
                "automod_exemptions",
                ["id", "server_id", "rule_id", "target_id", "created_at", "created_by"],
            ),
            ("automod_rate_tracking", ["id", "server_id", "user_id", "window_start"]),
        ]

        import re

        for table, columns in tables_to_fix:
            if table not in allowed_tables:
                continue
            if not db.table_exists(table):
                continue
            for col in columns:
                if not re.match(r"^[a-zA-Z0-9_]+$", col):
                    continue
                try:
                    # Use USING clause with safe conversion: valid integers are
                    # cast to BIGINT; non-numeric garbage (e.g. leaked column names)
                    # is set to NULL instead of failing the entire ALTER TABLE.
                    db.execute(
                        f'ALTER TABLE {table} ALTER COLUMN "{col}" TYPE BIGINT '
                        f"USING CASE WHEN \"{col}\" ~ '^[0-9]+$' "
                        f'THEN "{col}"::bigint ELSE NULL END'
                    )
                except Exception:
                    # Column may already be BIGINT or contain incompatible data
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
