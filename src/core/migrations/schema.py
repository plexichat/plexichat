"""
Migration tracking schema for the plexichat application.

This module defines the database schema for tracking applied migrations,
including the migrations_history table and associated functions.
"""

from src.core.database.core.schema_splitter import split_sql_statements

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS migrations_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version VARCHAR(20) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    applied_by VARCHAR(255) DEFAULT 'system',
    execution_time_ms INTEGER,
    checksum VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'completed',
    rollback_sql TEXT,
    error_message TEXT,
    is_irreversible BOOLEAN DEFAULT 0,
    depends_on TEXT,
    metadata TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_migrations_version ON migrations_history(version);
CREATE INDEX IF NOT EXISTS idx_migrations_status ON migrations_history(status);
CREATE INDEX IF NOT EXISTS idx_migrations_applied_at ON migrations_history(applied_at);

CREATE TABLE IF NOT EXISTS migration_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    migration_version VARCHAR(20) NOT NULL,
    level VARCHAR(20) NOT NULL,
    message TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (migration_version) REFERENCES migrations_history(version) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_migration_logs_version ON migration_logs(migration_version);
CREATE INDEX IF NOT EXISTS idx_migration_logs_timestamp ON migration_logs(timestamp);

CREATE TABLE IF NOT EXISTS migration_uptime (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    end_time TIMESTAMP,
    uptime_seconds INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS emergency_override_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token VARCHAR(64) NOT NULL UNIQUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP,
    used_by VARCHAR(255),
    reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_emergency_tokens_token ON emergency_override_tokens(token);
CREATE INDEX IF NOT EXISTS idx_emergency_tokens_expires ON emergency_override_tokens(expires_at);
"""

SCHEMA_POSTGRESQL = """
CREATE TABLE IF NOT EXISTS migrations_history (
    id SERIAL PRIMARY KEY,
    version VARCHAR(20) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    applied_by VARCHAR(255) DEFAULT 'system',
    execution_time_ms INTEGER,
    checksum VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'completed',
    rollback_sql TEXT,
    error_message TEXT,
    is_irreversible BOOLEAN DEFAULT FALSE,
    depends_on TEXT,
    metadata TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_migrations_version ON migrations_history(version);
CREATE INDEX IF NOT EXISTS idx_migrations_status ON migrations_history(status);
CREATE INDEX IF NOT EXISTS idx_migrations_applied_at ON migrations_history(applied_at);

CREATE TABLE IF NOT EXISTS migration_logs (
    id SERIAL PRIMARY KEY,
    migration_version VARCHAR(20) NOT NULL,
    level VARCHAR(20) NOT NULL,
    message TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (migration_version) REFERENCES migrations_history(version) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_migration_logs_version ON migration_logs(migration_version);
CREATE INDEX IF NOT EXISTS idx_migration_logs_timestamp ON migration_logs(timestamp);

CREATE TABLE IF NOT EXISTS migration_uptime (
    id SERIAL PRIMARY KEY,
    start_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    end_time TIMESTAMP,
    uptime_seconds INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS emergency_override_tokens (
    id SERIAL PRIMARY KEY,
    token VARCHAR(64) NOT NULL UNIQUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP,
    used_by VARCHAR(255),
    reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_emergency_tokens_token ON emergency_override_tokens(token);
CREATE INDEX IF NOT EXISTS idx_emergency_tokens_expires ON emergency_override_tokens(expires_at);
"""


def create_tables(db):
    """
    Create the migrations_history table in the database.

    Args:
        db: Database instance from plexichat.src.core.database

    Raises:
        Exception: If table creation fails
    """
    schema = SCHEMA_SQLITE if db.type == "sqlite" else SCHEMA_POSTGRESQL

    # Split statements and execute each one
    statements = split_sql_statements(schema)

    for statement in statements:
        db.execute(statement)
