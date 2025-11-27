"""
Server database schema - Table definitions for server module.
"""

import utils.logger as logger


SCHEMA = """
-- Servers table
CREATE TABLE IF NOT EXISTS srv_servers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    owner_id INTEGER NOT NULL,
    description TEXT,
    icon_url TEXT,
    default_role_id INTEGER,
    system_channel_id INTEGER,
    verification_level INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    deleted INTEGER DEFAULT 0,
    deleted_at INTEGER,
    metadata TEXT
);

-- Server indexes
CREATE INDEX IF NOT EXISTS idx_srv_servers_owner ON srv_servers(owner_id);
CREATE INDEX IF NOT EXISTS idx_srv_servers_deleted ON srv_servers(deleted);

-- Channel categories table
CREATE TABLE IF NOT EXISTS srv_categories (
    id INTEGER PRIMARY KEY,
    server_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    position INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (server_id) REFERENCES srv_servers(id)
);

-- Category indexes
CREATE INDEX IF NOT EXISTS idx_srv_categories_server ON srv_categories(server_id);

-- Channels table
CREATE TABLE IF NOT EXISTS srv_channels (
    id INTEGER PRIMARY KEY,
    server_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    channel_type TEXT NOT NULL DEFAULT 'text',
    category_id INTEGER,
    position INTEGER DEFAULT 0,
    topic TEXT,
    nsfw INTEGER DEFAULT 0,
    slowmode_seconds INTEGER DEFAULT 0,
    conversation_id INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    deleted INTEGER DEFAULT 0,
    deleted_at INTEGER,
    metadata TEXT,
    FOREIGN KEY (server_id) REFERENCES srv_servers(id),
    FOREIGN KEY (category_id) REFERENCES srv_categories(id)
);

-- Channel indexes
CREATE INDEX IF NOT EXISTS idx_srv_channels_server ON srv_channels(server_id);
CREATE INDEX IF NOT EXISTS idx_srv_channels_category ON srv_channels(category_id);
CREATE INDEX IF NOT EXISTS idx_srv_channels_conversation ON srv_channels(conversation_id);
CREATE INDEX IF NOT EXISTS idx_srv_channels_deleted ON srv_channels(deleted);

-- Roles table
CREATE TABLE IF NOT EXISTS srv_roles (
    id INTEGER PRIMARY KEY,
    server_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    permissions TEXT,
    color TEXT,
    hoist INTEGER DEFAULT 0,
    mentionable INTEGER DEFAULT 0,
    position INTEGER DEFAULT 0,
    is_default INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    deleted INTEGER DEFAULT 0,
    FOREIGN KEY (server_id) REFERENCES srv_servers(id)
);

-- Role indexes
CREATE INDEX IF NOT EXISTS idx_srv_roles_server ON srv_roles(server_id);
CREATE INDEX IF NOT EXISTS idx_srv_roles_default ON srv_roles(is_default);
CREATE INDEX IF NOT EXISTS idx_srv_roles_position ON srv_roles(server_id, position);

-- Members table
CREATE TABLE IF NOT EXISTS srv_members (
    id INTEGER PRIMARY KEY,
    server_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    nickname TEXT,
    joined_at INTEGER NOT NULL,
    muted INTEGER DEFAULT 0,
    deafened INTEGER DEFAULT 0,
    inviter_id INTEGER,
    FOREIGN KEY (server_id) REFERENCES srv_servers(id),
    UNIQUE(server_id, user_id)
);

-- Member indexes
CREATE INDEX IF NOT EXISTS idx_srv_members_server ON srv_members(server_id);
CREATE INDEX IF NOT EXISTS idx_srv_members_user ON srv_members(user_id);

-- Member roles junction table
CREATE TABLE IF NOT EXISTS srv_member_roles (
    id INTEGER PRIMARY KEY,
    member_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    assigned_at INTEGER NOT NULL,
    assigned_by INTEGER,
    FOREIGN KEY (member_id) REFERENCES srv_members(id),
    FOREIGN KEY (role_id) REFERENCES srv_roles(id),
    UNIQUE(member_id, role_id)
);

-- Member roles indexes
CREATE INDEX IF NOT EXISTS idx_srv_member_roles_member ON srv_member_roles(member_id);
CREATE INDEX IF NOT EXISTS idx_srv_member_roles_role ON srv_member_roles(role_id);

-- Channel permission overrides table
CREATE TABLE IF NOT EXISTS srv_channel_overrides (
    id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    allow TEXT,
    deny TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (channel_id) REFERENCES srv_channels(id),
    UNIQUE(channel_id, target_type, target_id)
);

-- Override indexes
CREATE INDEX IF NOT EXISTS idx_srv_overrides_channel ON srv_channel_overrides(channel_id);
CREATE INDEX IF NOT EXISTS idx_srv_overrides_target ON srv_channel_overrides(target_type, target_id);

-- Invites table
CREATE TABLE IF NOT EXISTS srv_invites (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    server_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    inviter_id INTEGER NOT NULL,
    max_age INTEGER DEFAULT 86400,
    max_uses INTEGER DEFAULT 0,
    uses INTEGER DEFAULT 0,
    temporary INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    expires_at INTEGER,
    revoked INTEGER DEFAULT 0,
    FOREIGN KEY (server_id) REFERENCES srv_servers(id),
    FOREIGN KEY (channel_id) REFERENCES srv_channels(id)
);

-- Invite indexes
CREATE INDEX IF NOT EXISTS idx_srv_invites_code ON srv_invites(code);
CREATE INDEX IF NOT EXISTS idx_srv_invites_server ON srv_invites(server_id);
CREATE INDEX IF NOT EXISTS idx_srv_invites_channel ON srv_invites(channel_id);

-- Bans table
CREATE TABLE IF NOT EXISTS srv_bans (
    id INTEGER PRIMARY KEY,
    server_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    banned_by INTEGER NOT NULL,
    reason TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (server_id) REFERENCES srv_servers(id),
    UNIQUE(server_id, user_id)
);

-- Ban indexes
CREATE INDEX IF NOT EXISTS idx_srv_bans_server ON srv_bans(server_id);
CREATE INDEX IF NOT EXISTS idx_srv_bans_user ON srv_bans(user_id);

-- Audit log table
CREATE TABLE IF NOT EXISTS srv_audit_log (
    id INTEGER PRIMARY KEY,
    server_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id INTEGER,
    changes TEXT,
    reason TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (server_id) REFERENCES srv_servers(id)
);

-- Audit log indexes
CREATE INDEX IF NOT EXISTS idx_srv_audit_server ON srv_audit_log(server_id);
CREATE INDEX IF NOT EXISTS idx_srv_audit_action ON srv_audit_log(action);
CREATE INDEX IF NOT EXISTS idx_srv_audit_created ON srv_audit_log(created_at);
"""


def create_tables(db):
    """Create all server tables."""
    statements = [s.strip() for s in SCHEMA.split(";") if s.strip()]
    
    for statement in statements:
        if statement:
            try:
                db.execute(statement)
            except Exception as e:
                logger.error(f"Failed to execute schema statement: {e}")
                raise
    
    logger.info("Server tables created successfully")
