"""
Database schema for authentication module.

Tables are auto-created on auth.setup() if they don't exist.
"""

# SQL statements for creating auth tables
SCHEMA_SQLITE = """
-- Users table
CREATE TABLE IF NOT EXISTS auth_users (
    id INTEGER PRIMARY KEY,
    account_type TEXT NOT NULL DEFAULT 'user',
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE,
    password_hash TEXT NOT NULL,
    permissions TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    email_verified INTEGER DEFAULT 0,
    account_locked INTEGER DEFAULT 0,
    locked_until INTEGER,
    failed_login_attempts INTEGER DEFAULT 0,
    last_login_at INTEGER,
    totp_secret_encrypted TEXT,
    totp_enabled INTEGER DEFAULT 0,
    backup_codes_hash TEXT,
    public_key BLOB
);

-- Sessions table
CREATE TABLE IF NOT EXISTS auth_sessions (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL,
    device_id INTEGER,
    ip_address TEXT,
    user_agent TEXT,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    last_activity INTEGER NOT NULL,
    revoked INTEGER DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE
);

-- Bots table
CREATE TABLE IF NOT EXISTS auth_bots (
    id INTEGER PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    username TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    permissions TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    disabled INTEGER DEFAULT 0,
    FOREIGN KEY (owner_id) REFERENCES auth_users(id) ON DELETE CASCADE
);

-- Devices table
CREATE TABLE IF NOT EXISTS auth_devices (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    name TEXT,
    device_type TEXT,
    first_seen_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
    UNIQUE(user_id, fingerprint)
);

-- Known IPs table
CREATE TABLE IF NOT EXISTS auth_known_ips (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    ip_address TEXT NOT NULL,
    first_seen_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
    UNIQUE(user_id, ip_address)
);

-- Audit log table
CREATE TABLE IF NOT EXISTS auth_audit_log (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    event_type TEXT NOT NULL,
    ip_address TEXT,
    device_id INTEGER,
    timestamp INTEGER NOT NULL,
    details TEXT,
    success INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE SET NULL
);

-- Email verification tokens
CREATE TABLE IF NOT EXISTS auth_email_tokens (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL,
    token_type TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used INTEGER DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE
);

-- 2FA challenge tokens (temporary during login)
CREATE TABLE IF NOT EXISTS auth_2fa_challenges (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL,
    device_id INTEGER,
    ip_address TEXT,
    user_agent TEXT,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used INTEGER DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_token ON auth_sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires ON auth_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_auth_bots_owner ON auth_bots(owner_id);
CREATE INDEX IF NOT EXISTS idx_auth_bots_token ON auth_bots(token_hash);
CREATE INDEX IF NOT EXISTS idx_auth_devices_user ON auth_devices(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_known_ips_user ON auth_known_ips(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_audit_user ON auth_audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_audit_timestamp ON auth_audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_auth_audit_type ON auth_audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_auth_email_tokens_hash ON auth_email_tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_auth_2fa_challenges_hash ON auth_2fa_challenges(token_hash);
"""

# Split into individual statements for execution
SCHEMA_STATEMENTS = [stmt.strip() for stmt in SCHEMA_SQLITE.split(";") if stmt.strip()]


def create_tables(db) -> None:
    """
    Create all auth tables if they don't exist.
    
    Args:
        db: Database instance (must be connected)
    """
    for statement in SCHEMA_STATEMENTS:
        if statement:
            db.execute(statement)


def drop_tables(db) -> None:
    """
    Drop all auth tables. USE WITH CAUTION.
    
    Args:
        db: Database instance (must be connected)
    """
    tables = [
        "auth_2fa_challenges",
        "auth_email_tokens",
        "auth_audit_log",
        "auth_known_ips",
        "auth_devices",
        "auth_bots",
        "auth_sessions",
        "auth_users",
    ]
    for table in tables:
        db.execute(f"DROP TABLE IF EXISTS {table}")
