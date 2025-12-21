"""
Database schema for user settings module.
"""

SCHEMA_SQLITE = """
-- User settings table
CREATE TABLE IF NOT EXISTS user_settings (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    key VARCHAR(100) NOT NULL,
    value TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
    UNIQUE(user_id, key)
);

-- Indexes for performance
-- Note: idx_user_settings_key removed - redundant with UNIQUE(user_id, key) constraint
CREATE INDEX IF NOT EXISTS idx_user_settings_user ON user_settings(user_id);
"""

SCHEMA_STATEMENTS = [stmt.strip() for stmt in SCHEMA_SQLITE.split(";") if stmt.strip()]


def create_tables(db) -> None:
    """Create user settings tables if they don't exist."""
    for statement in SCHEMA_STATEMENTS:
        if statement:
            converted = db.convert_schema(statement) if hasattr(db, 'convert_schema') else statement
            db.execute(converted)


def drop_tables(db) -> None:
    """Drop user settings tables. USE WITH CAUTION."""
    db.execute("DROP TABLE IF EXISTS user_settings")
