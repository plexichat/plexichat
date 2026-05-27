"""
Admin RBAC System Migration - Implements role-based access control for admin panel.

This migration adds:
- admin_roles table for role definitions with permissions
- admin_role_assignments table for role assignments
- admin_audit_log table for comprehensive audit logging
- admin_approvals table for approval workflows
- force_password_change column to admin_users
- Enhanced admin security features
"""

import logging
import time

logger = logging.getLogger(__name__)


def up(db):
    """Apply the migration."""
    logger.info("Migration 032: Starting Admin RBAC System")

    # === Admin Roles Table ===
    db.execute("""
        CREATE TABLE IF NOT EXISTS admin_roles (
            id INTEGER PRIMARY KEY,
            name VARCHAR(50) UNIQUE NOT NULL,
            description TEXT,
            permissions TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            created_by INTEGER NOT NULL,
            updated_at INTEGER,
            is_system INTEGER NOT NULL DEFAULT 0
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_admin_roles_name ON admin_roles(name)")

    # === Admin Role Assignments Table ===
    db.execute("""
        CREATE TABLE IF NOT EXISTS admin_role_assignments (
            admin_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            assigned_at INTEGER NOT NULL,
            assigned_by INTEGER NOT NULL,
            PRIMARY KEY (admin_id, role_id)
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_role_assignments_admin ON admin_role_assignments(admin_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_role_assignments_role ON admin_role_assignments(role_id)"
    )

    # === Admin Audit Log Table ===
    db.execute("""
        CREATE TABLE IF NOT EXISTS admin_audit_log (
            id INTEGER PRIMARY KEY,
            admin_id INTEGER NOT NULL,
            action VARCHAR(100) NOT NULL,
            target_type VARCHAR(50),
            target_id INTEGER,
            target_user_id INTEGER,
            details TEXT,
            ip_address VARCHAR(45),
            user_agent TEXT,
            status VARCHAR(20) NOT NULL DEFAULT 'success',
            created_at INTEGER NOT NULL
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_audit_admin ON admin_audit_log(admin_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_audit_action ON admin_audit_log(action)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_audit_target ON admin_audit_log(target_type, target_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit_log(created_at)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_audit_status ON admin_audit_log(status)"
    )

    # === Admin Approvals Table ===
    db.execute("""
        CREATE TABLE IF NOT EXISTS admin_approvals (
            id INTEGER PRIMARY KEY,
            requested_by INTEGER NOT NULL,
            action_type VARCHAR(100) NOT NULL,
            target_type VARCHAR(50),
            target_id INTEGER,
            action_details TEXT,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            required_approvals INTEGER NOT NULL DEFAULT 2,
            current_approvals INTEGER NOT NULL DEFAULT 0,
            approved_by TEXT,
            rejected_by INTEGER,
            rejection_reason TEXT,
            expires_at INTEGER,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_approvals_requested ON admin_approvals(requested_by)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_approvals_status ON admin_approvals(status)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_approvals_action ON admin_approvals(action_type)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_approvals_target ON admin_approvals(target_type, target_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_approvals_expires ON admin_approvals(expires_at)"
    )

    # === Add force_password_change to admin_users ===
    try:
        from src.core.database import dialect

        safe_table = dialect.sanitize_identifier("admin_users", db.type)
        db.execute(
            f"ALTER TABLE {safe_table} ADD COLUMN force_password_change INTEGER NOT NULL DEFAULT 0"
        )
    except Exception:
        # Column might already exist
        pass

    # === Add session_timeout to admin_users ===
    try:
        from src.core.database import dialect

        safe_table = dialect.sanitize_identifier("admin_users", db.type)
        db.execute(
            f"ALTER TABLE {safe_table} ADD COLUMN session_timeout_minutes INTEGER NOT NULL DEFAULT 480"
        )
    except Exception:
        pass

    # === Add max_concurrent_sessions to admin_users ===
    try:
        from src.core.database import dialect

        safe_table = dialect.sanitize_identifier("admin_users", db.type)
        db.execute(
            f"ALTER TABLE {safe_table} ADD COLUMN max_concurrent_sessions INTEGER NOT NULL DEFAULT 3"
        )
    except Exception:
        pass

    # === Add last_password_change to admin_users ===
    try:
        from src.core.database import dialect

        safe_table = dialect.sanitize_identifier("admin_users", db.type)
        db.execute(f"ALTER TABLE {safe_table} ADD COLUMN last_password_change INTEGER")
    except Exception:
        pass

    # === Insert default admin roles ===
    now = int(time.time() * 1000)

    # Get the first admin user (created during initial setup)
    admin_user = db.fetch_one("SELECT id FROM admin_users LIMIT 1")
    admin_id = admin_user["id"] if admin_user else 1

    # Check if roles already exist
    existing_roles = db.fetch_one("SELECT COUNT(*) as c FROM admin_roles")
    if existing_roles and existing_roles.get("c", 0) == 0:
        from src.utils.encryption import generate_snowflake_id

        # Super Admin - Full access
        super_admin_id = generate_snowflake_id()
        db.execute(
            """
            INSERT INTO admin_roles (id, name, description, permissions, created_at, created_by, is_system)
            VALUES (?, 'super_admin', 'Full system access with all permissions', '{"*": true}', ?, ?, 1)
        """,
            (super_admin_id, now, admin_id),
        )

        # Support Admin - User management and support
        support_admin_id = generate_snowflake_id()
        db.execute(
            """
            INSERT INTO admin_roles (id, name, description, permissions, created_at, created_by, is_system)
            VALUES (?, 'support_admin', 'User management and support access', 
            '{"users.read": true, "users.edit": true, "users.tier": true, "tickets.*": true, "users.notes": true}', ?, ?, 1)
        """,
            (support_admin_id, now, admin_id),
        )

        # Moderation Admin - Content moderation only
        moderation_admin_id = generate_snowflake_id()
        db.execute(
            """
            INSERT INTO admin_roles (id, name, description, permissions, created_at, created_by, is_system)
            VALUES (?, 'moderation_admin', 'Content moderation and user blocking', 
            '{"automod.*": true, "reports.*": true, "blocked_users.*": true, "blocked_hashes.*": true}', ?, ?, 1)
        """,
            (moderation_admin_id, now, admin_id),
        )

        # Read-Only Admin - Read-only access
        readonly_admin_id = generate_snowflake_id()
        db.execute(
            """
            INSERT INTO admin_roles (id, name, description, permissions, created_at, created_by, is_system)
            VALUES (?, 'readonly_admin', 'Read-only access to dashboard and metrics', 
            '{"*": false, "users.read": true, "servers.read": true, "metrics.read": true, "tickets.read": true}', ?, ?, 1)
        """,
            (readonly_admin_id, now, admin_id),
        )

        # Assign super_admin role to the first admin
        db.execute(
            """
            INSERT INTO admin_role_assignments (admin_id, role_id, assigned_at, assigned_by)
            VALUES (?, ?, ?, ?)
        """,
            (admin_id, super_admin_id, now, admin_id),
        )

        logger.info(
            f"Migration 032: Created default admin roles and assigned super_admin to admin {admin_id}"
        )
    else:
        logger.info(
            "Migration 032: Admin roles already exist, skipping default role creation"
        )

    logger.info("Migration 032: Admin RBAC System completed successfully")


def down(db):
    """Rollback the migration."""
    logger.info("Migration 032 rollback: Starting rollback")

    # Drop new tables
    tables = [
        "admin_approvals",
        "admin_audit_log",
        "admin_role_assignments",
        "admin_roles",
    ]

    for table in tables:
        if db.table_exists(table):
            db.execute(f"DROP TABLE IF EXISTS {table}")

    # For PostgreSQL, drop the added columns
    if db.type == "postgres":
        try:
            from src.core.database import dialect

            safe_table = dialect.sanitize_identifier("admin_users", db.type)

            if db.column_exists("admin_users", "force_password_change"):
                db.execute(
                    f"ALTER TABLE {safe_table} DROP COLUMN force_password_change"
                )
            if db.column_exists("admin_users", "session_timeout_minutes"):
                db.execute(
                    f"ALTER TABLE {safe_table} DROP COLUMN session_timeout_minutes"
                )
            if db.column_exists("admin_users", "max_concurrent_sessions"):
                db.execute(
                    f"ALTER TABLE {safe_table} DROP COLUMN max_concurrent_sessions"
                )
            if db.column_exists("admin_users", "last_password_change"):
                db.execute(f"ALTER TABLE {safe_table} DROP COLUMN last_password_change")

            logger.info(
                "Migration 032 rollback: Dropped tables and columns (PostgreSQL)"
            )
        except Exception as e:
            logger.warning(f"Migration 032 rollback error: {e}")
    else:
        # SQLite: Clear column values but leave columns
        try:
            if db.column_exists("admin_users", "force_password_change"):
                db.execute("UPDATE admin_users SET force_password_change = 0")
            if db.column_exists("admin_users", "session_timeout_minutes"):
                db.execute("UPDATE admin_users SET session_timeout_minutes = 480")
            if db.column_exists("admin_users", "max_concurrent_sessions"):
                db.execute("UPDATE admin_users SET max_concurrent_sessions = 3")

            logger.info(
                "Migration 032 rollback: Dropped tables, cleared column values (SQLite)"
            )
        except Exception as e:
            logger.warning(f"Migration 032 rollback error: {e}")

    logger.info("Migration 032 rollback completed")
