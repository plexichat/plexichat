"""
Add username blacklist table and user force_change flag.
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Apply the migration."""
    logger.info("Migration 007: Starting username blacklist addition")
    # 1. Add force_username_change column to users table
    try:
        if not db.column_exists("auth_users", "force_username_change"):
            if db.type == "sqlite":
                db.execute(
                    "ALTER TABLE auth_users ADD COLUMN force_username_change BOOLEAN DEFAULT 0"
                )
            else:
                # Postgres
                db.execute(
                    "ALTER TABLE auth_users ADD COLUMN force_username_change BOOLEAN DEFAULT FALSE"
                )
    except Exception as e:
        print(f"Warning: Could not add column force_username_change: {e}")

    # 2. Create username_blacklist table
    if db.type == "sqlite":
        db.execute("""
            CREATE TABLE IF NOT EXISTS username_blacklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern TEXT NOT NULL UNIQUE,
                is_regex BOOLEAN DEFAULT 0,
                reason TEXT,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    else:
        db.execute("""
            CREATE TABLE IF NOT EXISTS username_blacklist (
                id BIGSERIAL PRIMARY KEY,
                pattern TEXT NOT NULL UNIQUE,
                is_regex BOOLEAN DEFAULT FALSE,
                reason TEXT,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    # 3. Seed default blacklist
    try:
        count = db.fetch_one("SELECT COUNT(*) as c FROM username_blacklist")
        if count and count["c"] == 0:
            defaults = [
                ("admin", False, "Reserved role"),
                ("moderator", False, "Reserved role"),
                ("staff", False, "Reserved role"),
                ("system", False, "Reserved role"),
                ("plexichat", False, "Reserved brand"),
                ("root", False, "Reserved system user"),
                ("support", False, "Reserved role"),
                ("help", False, "Reserved role"),
                ("security", False, "Reserved role"),
                ("bot", False, "Reserved role"),
                ("discord", False, "Competitor"),
                ("slack", False, "Competitor"),
                ("abuse", False, "Reserved role"),
                ("owner", False, "Reserved role"),
            ]

            for pattern, is_regex, reason in defaults:
                try:
                    db.execute(
                        "INSERT INTO username_blacklist (pattern, is_regex, reason) VALUES (?, ?, ?)",
                        (pattern, is_regex, reason),
                    )
                except Exception:
                    pass
    except Exception as e:
        print(f"Warning: Could not seed blacklist: {e}")


def down(db):
    """Rollback the migration."""
    logger.info("Migration 007 rollback: Starting rollback")
    db.execute("DROP TABLE IF EXISTS username_blacklist")

    if db.type == "postgres":
        try:
            if db.column_exists("auth_users", "force_username_change"):
                db.execute("ALTER TABLE auth_users DROP COLUMN force_username_change")
        except Exception:
            pass
