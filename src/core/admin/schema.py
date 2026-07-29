"""
Admin database schema - Table definitions for admin module.

Tables are auto-created on admin.setup() if they don't exist.
All IDs use Snowflake format for distributed generation.
"""

import utils.logger as logger

from src.core.database.core.schema_splitter import split_sql_statements


SCHEMA = """
-- Admin users table
CREATE TABLE IF NOT EXISTS admin_users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    email TEXT,
    totp_secret TEXT,
    totp_secret_encrypted TEXT,
    totp_enabled INTEGER DEFAULT 0,
    backup_codes TEXT,
    backup_codes_hash TEXT,
    otp_last_used_code TEXT,
    otp_last_used_at INTEGER,
    created_at INTEGER NOT NULL,
    last_login INTEGER,
    must_setup_otp INTEGER DEFAULT 1,
    force_password_change INTEGER NOT NULL DEFAULT 0,
    session_timeout_minutes INTEGER NOT NULL DEFAULT 480,
    max_concurrent_sessions INTEGER NOT NULL DEFAULT 3,
    last_password_change INTEGER
);

-- Admin sessions table
CREATE TABLE IF NOT EXISTS admin_sessions (
    id INTEGER PRIMARY KEY,
    admin_id INTEGER NOT NULL,
    token TEXT UNIQUE NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    ip_address TEXT,
    user_agent TEXT,
    FOREIGN KEY (admin_id) REFERENCES admin_users(id)
);

-- Admin sessions indexes
CREATE INDEX IF NOT EXISTS idx_admin_sessions_token ON admin_sessions(token);
CREATE INDEX IF NOT EXISTS idx_admin_sessions_admin ON admin_sessions(admin_id);

-- Admin notes table
CREATE TABLE IF NOT EXISTS admin_notes (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL,
    admin_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (ticket_id) REFERENCES feedback(id),
    FOREIGN KEY (admin_id) REFERENCES admin_users(id)
);

-- Admin notes indexes
CREATE INDEX IF NOT EXISTS idx_admin_notes_ticket ON admin_notes(ticket_id);
CREATE INDEX IF NOT EXISTS idx_admin_notes_admin ON admin_notes(admin_id);

-- Admin roles table
CREATE TABLE IF NOT EXISTS admin_roles (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    permissions TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    created_by INTEGER NOT NULL,
    updated_at INTEGER,
    is_system INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 10
);

-- Admin roles indexes
CREATE INDEX IF NOT EXISTS idx_admin_roles_name ON admin_roles(name);
CREATE INDEX IF NOT EXISTS idx_admin_roles_position ON admin_roles(position);

-- Admin role assignments table
CREATE TABLE IF NOT EXISTS admin_role_assignments (
    admin_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    assigned_at INTEGER NOT NULL,
    assigned_by INTEGER NOT NULL,
    PRIMARY KEY (admin_id, role_id)
);

-- Admin role assignments indexes
CREATE INDEX IF NOT EXISTS idx_admin_role_assignments_admin ON admin_role_assignments(admin_id);
CREATE INDEX IF NOT EXISTS idx_admin_role_assignments_role ON admin_role_assignments(role_id);

-- Admin audit log table
CREATE TABLE IF NOT EXISTS admin_audit_log (
    id INTEGER PRIMARY KEY,
    admin_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id INTEGER,
    target_user_id INTEGER,
    details TEXT,
    ip_address TEXT,
    user_agent TEXT,
    status TEXT NOT NULL DEFAULT 'success',
    created_at INTEGER NOT NULL
);

-- Admin audit log indexes
CREATE INDEX IF NOT EXISTS idx_admin_audit_admin ON admin_audit_log(admin_id);
CREATE INDEX IF NOT EXISTS idx_admin_audit_action ON admin_audit_log(action);
CREATE INDEX IF NOT EXISTS idx_admin_audit_target ON admin_audit_log(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_admin_audit_status ON admin_audit_log(status);

-- Admin approvals table
CREATE TABLE IF NOT EXISTS admin_approvals (
    id INTEGER PRIMARY KEY,
    requested_by INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    target_type TEXT,
    target_id INTEGER,
    action_details TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    required_approvals INTEGER NOT NULL DEFAULT 2,
    current_approvals INTEGER NOT NULL DEFAULT 0,
    approved_by INTEGER,
    rejected_by INTEGER,
    rejection_reason TEXT,
    expires_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- Admin approvals indexes
CREATE INDEX IF NOT EXISTS idx_admin_approvals_requested ON admin_approvals(requested_by);
CREATE INDEX IF NOT EXISTS idx_admin_approvals_status ON admin_approvals(status);
CREATE INDEX IF NOT EXISTS idx_admin_approvals_action ON admin_approvals(action_type);
CREATE INDEX IF NOT EXISTS idx_admin_approvals_target ON admin_approvals(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_admin_approvals_expires ON admin_approvals(expires_at);

-- Admin notes versioning table
CREATE TABLE IF NOT EXISTS admin_notes_versioning (
    id INTEGER PRIMARY KEY,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    note_content TEXT NOT NULL,
    note_format TEXT DEFAULT 'plain',
    created_by INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    version_number INTEGER NOT NULL,
    change_reason TEXT
);

-- Admin notes versioning indexes
CREATE INDEX IF NOT EXISTS idx_admin_notes_target ON admin_notes_versioning(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_admin_notes_created ON admin_notes_versioning(created_at);
CREATE INDEX IF NOT EXISTS idx_admin_notes_author ON admin_notes_versioning(created_by);

-- Admin approval comments table
CREATE TABLE IF NOT EXISTS admin_approval_comments (
    id INTEGER PRIMARY KEY,
    approval_id INTEGER NOT NULL,
    admin_id INTEGER NOT NULL,
    comment TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (approval_id) REFERENCES admin_approvals(id) ON DELETE CASCADE
);

-- Admin approval comments indexes
CREATE INDEX IF NOT EXISTS idx_admin_approval_comments_approval ON admin_approval_comments(approval_id);
CREATE INDEX IF NOT EXISTS idx_admin_approval_comments_admin ON admin_approval_comments(admin_id);
"""


def create_admin_tables(db):
    """Create all admin tables."""
    statements = split_sql_statements(SCHEMA)

    for statement in statements:
        if statement:
            try:
                converted = (
                    db.convert_schema(statement)
                    if hasattr(db, "convert_schema")
                    else statement
                )
                db.execute(converted)
            except Exception as e:
                logger.error(f"Failed to execute schema statement: {e}")
                raise

    logger.info("Admin tables created successfully")
