"""
Harden admin security by ensuring admin tables exist with correct schema.
Transition admin schema management to the migration system.
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Apply the migration."""
    logger.info("Migration 008: Starting admin security hardening")
    logger.debug("Migration 008: Starting admin security hardening")

    # 1. Create admin_users table
    if db.type == "sqlite":
        db.execute("""
            CREATE TABLE IF NOT EXISTS admin_users (
                id INTEGER PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                email TEXT,
                totp_secret TEXT,
                totp_enabled INTEGER DEFAULT 0,
                backup_codes TEXT,
                created_at INTEGER NOT NULL,
                last_login INTEGER,
                must_setup_otp INTEGER DEFAULT 1
            )
        """)
    else:
        # PostgreSQL
        db.execute("""
            CREATE TABLE IF NOT EXISTS admin_users (
                id BIGINT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                email TEXT,
                totp_secret TEXT,
                totp_enabled INTEGER DEFAULT 0,
                backup_codes TEXT,
                created_at BIGINT NOT NULL,
                last_login BIGINT,
                must_setup_otp INTEGER DEFAULT 1
            )
        """)

    # 2. Create admin_sessions table
    if db.type == "sqlite":
        db.execute("""
            CREATE TABLE IF NOT EXISTS admin_sessions (
                id INTEGER PRIMARY KEY,
                admin_id INTEGER NOT NULL,
                token TEXT UNIQUE NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                ip_address TEXT,
                user_agent TEXT,
                FOREIGN KEY (admin_id) REFERENCES admin_users(id)
            )
        """)
    else:
        db.execute("""
            CREATE TABLE IF NOT EXISTS admin_sessions (
                id BIGINT PRIMARY KEY,
                admin_id BIGINT NOT NULL,
                token TEXT UNIQUE NOT NULL,
                created_at BIGINT NOT NULL,
                expires_at BIGINT NOT NULL,
                ip_address TEXT,
                user_agent TEXT,
                FOREIGN KEY (admin_id) REFERENCES admin_users(id)
            )
        """)

    # 3. Create admin_notes table
    if db.type == "sqlite":
        db.execute("""
            CREATE TABLE IF NOT EXISTS admin_notes (
                id INTEGER PRIMARY KEY,
                ticket_id INTEGER NOT NULL,
                admin_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (ticket_id) REFERENCES feedback(id),
                FOREIGN KEY (admin_id) REFERENCES admin_users(id)
            )
        """)
    else:
        db.execute("""
            CREATE TABLE IF NOT EXISTS admin_notes (
                id BIGINT PRIMARY KEY,
                ticket_id INTEGER NOT NULL,
                admin_id BIGINT NOT NULL,
                content TEXT NOT NULL,
                created_at BIGINT NOT NULL,
                FOREIGN KEY (ticket_id) REFERENCES feedback(id),
                FOREIGN KEY (admin_id) REFERENCES admin_users(id)
            )
        """)

    # 4. Create indexes
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_notes_ticket ON admin_notes(ticket_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_sessions_token ON admin_sessions(token)"
    )


def down(db):
    """Rollback the migration.

    For PostgreSQL: Drops the admin tables.
    For SQLite: Drops the admin tables (safe as they were created by this migration).
    """
    logger.info("Migration 008 rollback: Starting rollback")
    tables = ["admin_users", "admin_sessions", "admin_notes"]
    for table in tables:
        if db.table_exists(table):
            db.execute(f"DROP TABLE IF EXISTS {table}")
    logger.info("Migration 008 rollback: Dropped admin tables")
