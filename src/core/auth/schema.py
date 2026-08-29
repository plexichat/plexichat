"""
Hardened schema for authentication module.
Adds blind indexes for encrypted fields and improved integrity.
"""

from src.core.database.core.schema_splitter import split_sql_statements

import utils.logger as logger

SCHEMA_SQLITE = """
-- Users table
CREATE TABLE IF NOT EXISTS auth_users (
    id INTEGER PRIMARY KEY,
    account_type TEXT NOT NULL DEFAULT 'user',
    username TEXT UNIQUE NOT NULL,
    display_name_encrypted TEXT,
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
    -- Passkey state (canonical initial schema)
    passkey_enabled INTEGER DEFAULT 0,
    webauthn_user_handle TEXT,
    totp_secret_encrypted TEXT,
    backup_codes_hash TEXT,
    avatar_url TEXT,
    age_verified INTEGER DEFAULT 0,
    date_of_birth TEXT,
    force_username_change INTEGER DEFAULT 0,
    -- Internal admin notes about the user
    internal_notes TEXT,
    internal_notes_format TEXT DEFAULT 'plain',
    internal_notes_encrypted TEXT,
    -- Account deletion (017)
    deletion_status TEXT NOT NULL DEFAULT 'active',
    deletion_at BIGINT,
    -- Custom status (019)
    custom_status_text TEXT,
    custom_status_emoji TEXT,
    custom_status_expires_at INTEGER,
    -- Login/ban reason (commit db920196)
    reason_encrypted TEXT
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
    -- Blind index for User-Agent matching
    ua_index TEXT,
    -- Encrypted User-Agent for display
    ua_encrypted TEXT,
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
    display_name_encrypted TEXT,
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
    name_encrypted TEXT,
    device_type_encrypted TEXT,
    fingerprint_encrypted TEXT,
    trusted INTEGER DEFAULT 0,
    trusted_at INTEGER,
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
    reason_encrypted TEXT,
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
    expires_at INTEGER NOT NULL,
    used INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE
);

-- User notes table (Private notes about other users)
CREATE TABLE IF NOT EXISTS auth_user_notes (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    target_user_id INTEGER NOT NULL,
    note_encrypted TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
    FOREIGN KEY (target_user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
    UNIQUE(user_id, target_user_id)
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
    ua_index TEXT,
    ua_encrypted TEXT,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used INTEGER DEFAULT 0,
    -- Number of failed verification attempts for this challenge
    attempts INTEGER DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE
);

-- External accounts (OAuth)
CREATE TABLE IF NOT EXISTS auth_external_accounts (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    external_id_encrypted TEXT,
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

CREATE TABLE IF NOT EXISTS auth_api_access_tokens (
    id INTEGER PRIMARY KEY,
    name TEXT,
    description TEXT,
    token_index TEXT UNIQUE NOT NULL,
    token_encrypted TEXT NOT NULL,
    created_by INTEGER,
    created_at INTEGER NOT NULL,
    first_used_at INTEGER,
    last_used_at INTEGER,
    last_used_ip_index TEXT,
    last_used_ip_encrypted TEXT,
    ua_index TEXT,
    ua_encrypted TEXT,
    last_used_path TEXT,
    expires_at INTEGER,
    scope_mode TEXT NOT NULL DEFAULT 'none',
    use_count_total INTEGER NOT NULL DEFAULT 0,
    revoked INTEGER DEFAULT 0,
    revoked_at INTEGER,
    revoked_by INTEGER
);

CREATE TABLE IF NOT EXISTS auth_api_access_token_scopes (
    id INTEGER PRIMARY KEY,
    token_id INTEGER NOT NULL,
    scope_type TEXT NOT NULL,
    value TEXT NOT NULL,
    created_by INTEGER,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (token_id) REFERENCES auth_api_access_tokens(id) ON DELETE CASCADE,
    UNIQUE(token_id, scope_type, value)
);

CREATE TABLE IF NOT EXISTS auth_api_access_token_events (
    id INTEGER PRIMARY KEY,
    token_id INTEGER NOT NULL,
    used_at INTEGER NOT NULL,
    ip_index TEXT,
    ip_encrypted TEXT,
    method TEXT,
    path TEXT,
    ua_index TEXT,
    ua_encrypted TEXT,
    allowed INTEGER NOT NULL DEFAULT 1,
    scope_match INTEGER,
    reject_reason TEXT,
    FOREIGN KEY (token_id) REFERENCES auth_api_access_tokens(id) ON DELETE CASCADE
);

-- Failed login tracking per (user, IP) for distributed-lockout gating.
-- DDL lives in ensure_failed_logins_table() below (single source of truth);
-- create_tables() invokes it alongside the inline statements.

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
CREATE INDEX IF NOT EXISTS idx_auth_user_notes_user ON auth_user_notes(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_user_notes_target ON auth_user_notes(target_user_id);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_token ON auth_sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_auth_bots_owner ON auth_bots(owner_id);
CREATE INDEX IF NOT EXISTS idx_auth_devices_user ON auth_devices(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_audit_user ON auth_audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_api_access_tokens_index ON auth_api_access_tokens(token_index);
CREATE INDEX IF NOT EXISTS idx_auth_api_access_tokens_revoked ON auth_api_access_tokens(revoked);
CREATE INDEX IF NOT EXISTS idx_auth_api_access_tokens_expires ON auth_api_access_tokens(expires_at);
CREATE INDEX IF NOT EXISTS idx_auth_api_access_token_scopes_token ON auth_api_access_token_scopes(token_id);
CREATE INDEX IF NOT EXISTS idx_auth_api_access_token_events_token_time ON auth_api_access_token_events(token_id, used_at DESC);
CREATE INDEX IF NOT EXISTS idx_auth_api_access_token_events_ip ON auth_api_access_token_events(ip_index);

-- WebAuthn/FIDO2 Passkey credentials
CREATE TABLE IF NOT EXISTS auth_passkeys (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    credential_id TEXT NOT NULL,
    credential_public_key BLOB NOT NULL,
    sign_count INTEGER DEFAULT 0,
    device_type TEXT,
    device_name TEXT,
    device_name_encrypted TEXT,
    aaguid TEXT,
    transports TEXT,
    backed_up INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    last_used_at INTEGER,
    revoked INTEGER DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
    UNIQUE(credential_id)
);

-- Passkey challenges (temporary)
CREATE TABLE IF NOT EXISTS auth_passkey_challenges (
    id INTEGER PRIMARY KEY,
    challenge_id TEXT UNIQUE NOT NULL,
    user_id INTEGER,
    challenge_type TEXT NOT NULL,
    challenge BLOB NOT NULL,
    device_name TEXT,
    expires_at INTEGER NOT NULL,
    used INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL
);

-- Username blacklist table (used by auth blacklist checks)
CREATE TABLE IF NOT EXISTS username_blacklist (
    id INTEGER PRIMARY KEY,
    pattern TEXT NOT NULL UNIQUE,
    is_regex BOOLEAN DEFAULT 0,
    reason TEXT,
    created_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Account deletion records (017)
CREATE TABLE IF NOT EXISTS auth_deletion_records (
    id BIGINT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    identifier_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    scheduled_at BIGINT NOT NULL,
    purged_at BIGINT,
    audit_log_checksum TEXT,
    UNIQUE(user_id)
);

-- Passkey indexes
CREATE INDEX IF NOT EXISTS idx_auth_passkeys_user ON auth_passkeys(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_passkeys_credential ON auth_passkeys(credential_id);
CREATE INDEX IF NOT EXISTS idx_auth_passkey_challenges_expires ON auth_passkey_challenges(expires_at);
CREATE INDEX IF NOT EXISTS idx_auth_passkey_challenges_id ON auth_passkey_challenges(challenge_id);

-- Indexes for auth_users (017, 005)
CREATE INDEX IF NOT EXISTS idx_auth_users_deletion_status ON auth_users(deletion_status);
CREATE INDEX IF NOT EXISTS idx_auth_users_deletion_at ON auth_users(deletion_at);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_ip_index ON auth_sessions(ip_index);
CREATE INDEX IF NOT EXISTS idx_auth_known_ips_ip_index ON auth_known_ips(ip_index);

-- Index for auth_deletion_records
CREATE INDEX IF NOT EXISTS idx_auth_deletion_records_user ON auth_deletion_records(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_deletion_records_hash ON auth_deletion_records(identifier_hash);

-- Case-insensitive username uniqueness (prevents lookalike impersonation)
CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_users_username_lower
    ON auth_users(LOWER(username));
"""


# Single source of truth for the failed-login tracking DDL. Called from
# create_tables() for fresh installs/migrations and lazily from the session
# manager so databases created before this table existed are covered too.
_FAILED_LOGINS_DDL = (
    """
    CREATE TABLE IF NOT EXISTS auth_failed_logins (
        user_id INTEGER NOT NULL,
        ip_index TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_attempt_at INTEGER NOT NULL,
        PRIMARY KEY (user_id, ip_index)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_auth_failed_logins_user_time
        ON auth_failed_logins(user_id, last_attempt_at)
    """,
)


def ensure_failed_logins_table(db) -> None:
    for sql in _FAILED_LOGINS_DDL:
        converted = db.convert_schema(sql) if hasattr(db, "convert_schema") else sql
        db.execute(converted)


# Trusted-device trust flag: databases created before the flag existed get the
# columns added lazily here (single source of truth, same pattern as the
# failed-logins table).
_TRUSTED_DEVICES_DDL = (
    """
    ALTER TABLE auth_devices ADD COLUMN trusted INTEGER DEFAULT 0
    """,
    """
    ALTER TABLE auth_devices ADD COLUMN trusted_at INTEGER
    """,
)


def ensure_trusted_devices_columns(db) -> None:
    """Add the trusted-device columns when they do not exist yet.

    ALTER TABLE has no IF NOT EXISTS in SQLite/Postgres, so probe the table
    definition and only issue the ALTER when the column is missing.
    """
    try:
        rows = db.fetch_all(
            "SELECT name FROM pragma_table_info('auth_devices') AS t"
            if hasattr(db, "dialect") and getattr(db, "dialect", None) == "postgres"
            else "PRAGMA table_info(auth_devices)"
        )
        existing = {r["name"] for r in rows}
    except Exception:
        # Cannot introspect (unusual driver): attempt the ALTERs and ignore
        # duplicate-column failures.
        existing = set()

    for sql in _TRUSTED_DEVICES_DDL:
        column = "trusted_at" if "trusted_at" in sql else "trusted"
        if column in existing:
            continue
        try:
            converted = db.convert_schema(sql) if hasattr(db, "convert_schema") else sql
            db.execute(converted)
        except Exception:
            # Column already exists (race or fresh schema); safe to ignore.
            logger.debug("trusted-device column already present", exc_info=True)


_WS_TICKETS_DDL = (
    """
    CREATE TABLE IF NOT EXISTS auth_ws_tickets (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        session_id INTEGER,
        ticket_hash TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        used INTEGER DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_auth_ws_tickets_expires
        ON auth_ws_tickets(expires_at)
    """,
)


def ensure_ws_tickets_table(db) -> None:
    for sql in _WS_TICKETS_DDL:
        converted = db.convert_schema(sql) if hasattr(db, "convert_schema") else sql
        db.execute(converted)


def create_tables(db) -> None:
    statements = split_sql_statements(SCHEMA_SQLITE)
    for statement in statements:
        # Convert schema types for PostgreSQL compatibility (BLOB -> BYTEA, etc.)
        converted = (
            db.convert_schema(statement) if hasattr(db, "convert_schema") else statement
        )
        db.execute(converted)
    ensure_failed_logins_table(db)
    ensure_trusted_devices_columns(db)
    ensure_ws_tickets_table(db)
