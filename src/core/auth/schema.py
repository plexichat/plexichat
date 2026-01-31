"""
Hardened schema for authentication module.
Adds blind indexes for encrypted fields and improved integrity.
"""

SCHEMA_SQLITE = """
-- Users table
CREATE TABLE IF NOT EXISTS auth_users (
    id INTEGER PRIMARY KEY,
    account_type TEXT NOT NULL DEFAULT 'user',
    username TEXT UNIQUE NOT NULL,
    -- Blind index for email lookups
    email_index TEXT UNIQUE,
    -- Encrypted email
    email_encrypted TEXT,
    password_hash TEXT NOT NULL,
    permissions TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    email_verified INTEGER DEFAULT 0,
    account_locked INTEGER DEFAULT 0,
    locked_until INTEGER,
    failed_login_attempts INTEGER DEFAULT 0,
    last_login_at INTEGER,
    totp_enabled INTEGER DEFAULT 0,
    totp_secret_encrypted TEXT,
    backup_codes_hash TEXT,
    avatar_url TEXT,
    age_verified INTEGER DEFAULT 0,
    date_of_birth TEXT
);

-- Sessions table with Token Binding
CREATE TABLE IF NOT EXISTS auth_sessions (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL,
    device_id INTEGER,
    -- Blind index for IP matching
    ip_index TEXT,
    -- Encrypted IP for display
    ip_encrypted TEXT,
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

-- Devices table (Restored)
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

-- Known IPs table (Restored)
CREATE TABLE IF NOT EXISTS auth_known_ips (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    ip_index TEXT NOT NULL,
    ip_encrypted TEXT,
    first_seen_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
    UNIQUE(user_id, ip_index)
);

-- IP Blacklist table
CREATE TABLE IF NOT EXISTS auth_ip_blacklist (
    ip_index TEXT PRIMARY KEY,
    ip_encrypted TEXT,
    reason TEXT,
    blocked_at INTEGER NOT NULL,
    blocked_by INTEGER,
    expires_at INTEGER
);

-- Email verification tokens (Restored)
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

-- 2FA challenge tokens (Restored)
CREATE TABLE IF NOT EXISTS auth_2fa_challenges (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL,
    device_id INTEGER,
    -- Encrypted IP address
    ip_index TEXT,
    ip_encrypted TEXT,
    user_agent TEXT,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used INTEGER DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE
);

-- External accounts (OAuth)
CREATE TABLE IF NOT EXISTS auth_external_accounts (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    email_index TEXT,
    created_at INTEGER NOT NULL,
    last_login_at INTEGER,
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
    UNIQUE(provider, external_id)
);

-- Internal service secrets (for secure inter-service auth)
CREATE TABLE IF NOT EXISTS auth_internal_secrets (
    id INTEGER PRIMARY KEY,
    service_name TEXT UNIQUE NOT NULL,
    secret_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

-- Audit log with integrity
CREATE TABLE IF NOT EXISTS auth_audit_log (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    event_type TEXT NOT NULL,
    ip_index TEXT,
    ip_encrypted TEXT,
    device_id INTEGER,
    timestamp INTEGER NOT NULL,
    details_encrypted TEXT,
    success INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE SET NULL
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_auth_users_email_index ON auth_users(email_index);        
CREATE INDEX IF NOT EXISTS idx_auth_sessions_token ON auth_sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_auth_bots_owner ON auth_bots(owner_id);
CREATE INDEX IF NOT EXISTS idx_auth_devices_user ON auth_devices(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_audit_user ON auth_audit_log(user_id);
"""


def create_tables(db) -> None:
    statements = [stmt.strip() for stmt in SCHEMA_SQLITE.split(";") if stmt.strip()]
    for statement in statements:
        # Convert schema types for PostgreSQL compatibility (BLOB -> BYTEA, etc.)
        converted = (
            db.convert_schema(statement) if hasattr(db, "convert_schema") else statement
        )
        db.execute(converted)
