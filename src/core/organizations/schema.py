"""
Database schema for organizations module.
"""

import utils.logger as logger

SCHEMA_SQLITE = """
-- Organizations table
CREATE TABLE IF NOT EXISTS organizations (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    root_user_id INTEGER NOT NULL,
    is_default INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    settings TEXT,
    default_servers TEXT,
    allowed_servers TEXT,
    blocked_servers TEXT,
    allow_invites INTEGER DEFAULT 1,
    invite_requires_approval INTEGER DEFAULT 1,
    FOREIGN KEY (root_user_id) REFERENCES auth_users(id)
);

-- Organization indexes
CREATE INDEX IF NOT EXISTS idx_organizations_name ON organizations(name);
CREATE INDEX IF NOT EXISTS idx_organizations_root ON organizations(root_user_id);
CREATE INDEX IF NOT EXISTS idx_organizations_default ON organizations(is_default);

-- Organization members table
CREATE TABLE IF NOT EXISTS org_members (
    id INTEGER PRIMARY KEY,
    org_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL UNIQUE,
    role TEXT NOT NULL DEFAULT 'member',
    joined_at INTEGER NOT NULL,
    invited_by INTEGER,
    FOREIGN KEY (org_id) REFERENCES organizations(id),
    FOREIGN KEY (user_id) REFERENCES auth_users(id)
);

-- Member indexes
CREATE INDEX IF NOT EXISTS idx_org_members_org ON org_members(org_id);
CREATE INDEX IF NOT EXISTS idx_org_members_user ON org_members(user_id);
"""

SCHEMA_SQLITE_PART2 = """
-- Organization managed settings table
CREATE TABLE IF NOT EXISTS org_managed_settings (
    id INTEGER PRIMARY KEY,
    org_id INTEGER NOT NULL,
    setting_key TEXT NOT NULL,
    setting_value TEXT,
    locked INTEGER DEFAULT 1,
    FOREIGN KEY (org_id) REFERENCES organizations(id),
    UNIQUE(org_id, setting_key)
);

-- Managed settings indexes
CREATE INDEX IF NOT EXISTS idx_org_settings_org ON org_managed_settings(org_id);

-- Organization invites table
CREATE TABLE IF NOT EXISTS org_invites (
    id INTEGER PRIMARY KEY,
    org_id INTEGER NOT NULL,
    code TEXT UNIQUE NOT NULL,
    invite_type TEXT NOT NULL,
    target_username TEXT,
    target_user_id INTEGER,
    created_by INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER,
    max_uses INTEGER DEFAULT 1,
    uses INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    user_accepted INTEGER DEFAULT 0,
    user_accepted_at INTEGER,
    root_approved INTEGER DEFAULT 0,
    root_approved_at INTEGER,
    FOREIGN KEY (org_id) REFERENCES organizations(id)
);

-- Invite indexes
CREATE INDEX IF NOT EXISTS idx_org_invites_org ON org_invites(org_id);
CREATE INDEX IF NOT EXISTS idx_org_invites_code ON org_invites(code);
CREATE INDEX IF NOT EXISTS idx_org_invites_target ON org_invites(target_user_id);
CREATE INDEX IF NOT EXISTS idx_org_invites_status ON org_invites(status);
"""

SCHEMA_STATEMENTS = [stmt.strip() for stmt in (SCHEMA_SQLITE + SCHEMA_SQLITE_PART2).split(";") if stmt.strip()]


def create_tables(db) -> None:
    """Create organization tables if they don't exist."""
    for statement in SCHEMA_STATEMENTS:
        if statement:
            try:
                converted = db.convert_schema(statement) if hasattr(db, 'convert_schema') else statement
                db.execute(converted)
            except Exception as e:
                logger.error(f"Failed to execute schema statement: {e}")
                raise
    
    logger.info("Organization tables created successfully")


def add_org_columns_to_users(db) -> None:
    """Add org_id and managed_by_org columns to auth_users if they don't exist."""
    try:
        # Check if columns exist
        row = db.fetch_one("SELECT sql FROM sqlite_master WHERE type='table' AND name='auth_users'")
        if row and row["sql"]:
            sql = row["sql"].lower()
            
            if "org_id" not in sql:
                db.execute("ALTER TABLE auth_users ADD COLUMN org_id INTEGER")
                logger.info("Added org_id column to auth_users")
            
            if "managed_by_org" not in sql:
                db.execute("ALTER TABLE auth_users ADD COLUMN managed_by_org INTEGER DEFAULT 0")
                logger.info("Added managed_by_org column to auth_users")
    except Exception as e:
        # For PostgreSQL or if table doesn't exist yet
        try:
            db.execute("ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS org_id INTEGER")
            db.execute("ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS managed_by_org INTEGER DEFAULT 0")
        except Exception:
            logger.debug(f"Could not add org columns (may already exist): {e}")


def drop_tables(db) -> None:
    """Drop organization tables. USE WITH CAUTION."""
    tables = [
        "org_invites",
        "org_managed_settings",
        "org_members",
        "organizations",
    ]
    for table in tables:
        db.execute(f"DROP TABLE IF EXISTS {table}")
