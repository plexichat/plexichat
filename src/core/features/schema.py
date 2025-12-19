"""
Database schema for user features module.
"""

import utils.logger as logger

SCHEMA_SQLITE = """
-- User features table (admin-controlled feature flags and badges)
CREATE TABLE IF NOT EXISTS user_features (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE,
    
    -- Rate limiting tier
    rate_limit_tier TEXT DEFAULT 'standard',
    
    -- Badges (JSON array of badge names)
    badges TEXT DEFAULT '[]',
    
    -- Metadata
    granted_by INTEGER,
    granted_at INTEGER,
    expires_at INTEGER,
    notes TEXT,
    
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_user_features_user ON user_features(user_id);
CREATE INDEX IF NOT EXISTS idx_user_features_tier ON user_features(rate_limit_tier);
CREATE INDEX IF NOT EXISTS idx_user_features_expires ON user_features(expires_at);

-- Feature usage tracking (for limits like voice minutes, file uploads)
CREATE TABLE IF NOT EXISTS user_feature_usage (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    usage_type TEXT NOT NULL,
    usage_date TEXT NOT NULL,
    usage_count INTEGER DEFAULT 0,
    usage_value INTEGER DEFAULT 0,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
    UNIQUE(user_id, usage_type, usage_date)
);

-- Indexes for usage tracking
CREATE INDEX IF NOT EXISTS idx_feature_usage_user ON user_feature_usage(user_id);
CREATE INDEX IF NOT EXISTS idx_feature_usage_type ON user_feature_usage(usage_type);
CREATE INDEX IF NOT EXISTS idx_feature_usage_date ON user_feature_usage(usage_date);

-- Feature audit log (tracks admin changes to user features)
CREATE TABLE IF NOT EXISTS user_features_audit (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    admin_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE
);

-- Indexes for audit log
CREATE INDEX IF NOT EXISTS idx_features_audit_user ON user_features_audit(user_id);
CREATE INDEX IF NOT EXISTS idx_features_audit_admin ON user_features_audit(admin_id);
CREATE INDEX IF NOT EXISTS idx_features_audit_created ON user_features_audit(created_at);
"""

SCHEMA_STATEMENTS = [stmt.strip() for stmt in SCHEMA_SQLITE.split(";") if stmt.strip()]


def create_tables(db) -> None:
    """Create user features tables if they don't exist."""
    for statement in SCHEMA_STATEMENTS:
        if statement:
            try:
                converted = db.convert_schema(statement) if hasattr(db, 'convert_schema') else statement
                db.execute(converted)
            except Exception as e:
                logger.error(f"Failed to execute schema statement: {e}")
                raise

    logger.info("User features tables created successfully")


def drop_tables(db) -> None:
    """Drop user features tables. USE WITH CAUTION."""
    tables = [
        "user_features_audit",
        "user_feature_usage",
        "user_features",
    ]
    for table in tables:
        db.execute(f"DROP TABLE IF EXISTS {table}")
