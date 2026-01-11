"""
Migration tracking schema for the plexichat application.

This module defines the database schema for tracking applied migrations,
including the migrations_history table and associated functions.
"""

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
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_migrations_version ON migrations_history(version);
CREATE INDEX IF NOT EXISTS idx_migrations_status ON migrations_history(status);
CREATE INDEX IF NOT EXISTS idx_migrations_applied_at ON migrations_history(applied_at);
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
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_migrations_version ON migrations_history(version);
CREATE INDEX IF NOT EXISTS idx_migrations_status ON migrations_history(status);
CREATE INDEX IF NOT EXISTS idx_migrations_applied_at ON migrations_history(applied_at);
"""


def create_tables(db):
    """
    Create the migrations_history table in the database.
    
    Args:
        db: Database instance from plexichat.src.core.database
        
    Raises:
        Exception: If table creation fails
    """
    schema = SCHEMA_SQLITE if db.type == 'sqlite' else SCHEMA_POSTGRESQL
    
    # Split statements and execute each one
    statements = [
        stmt.strip() for stmt in schema.split(';') if stmt.strip()
    ]
    
    for statement in statements:
        db.execute(statement)
