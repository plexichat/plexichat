"""
Admin permissions system - Role-based access control for admin operations.

This module provides:
- Permission checking functions
- Role management utilities
- Permission scope definitions
- Approval workflow integration
"""

import json
import time
from typing import List, Dict, Any, Optional, Union
from dataclasses import dataclass
import utils.logger as logger


@dataclass
class AdminRole:
    """Admin role definition."""

    id: int
    name: str
    description: str
    permissions: Dict[str, bool]
    created_at: int
    created_by: int
    is_system: bool = False


@dataclass
class AdminPermission:
    """Permission check result."""

    has_permission: bool
    reason: Optional[str] = None
    requires_approval: bool = False
    approval_id: Optional[int] = None


# Permission scope definitions
PERMISSION_SCOPES = {
    # User management
    "users.read": "View user information and profiles",
    "users.edit": "Edit user profiles and basic settings",
    "users.delete": "Delete user accounts",
    "users.force_purge": "Immediately purge user accounts (dangerous)",
    "users.tier": "Modify user account tiers",
    "users.badges": "Manage user badges",
    "users.notes": "View and edit internal admin notes",
    "users.lock": "Lock/unlock user accounts",
    "users.force_username_change": "Force username changes",
    # Server management
    "servers.read": "View server information",
    "servers.edit": "Edit server settings",
    "servers.delete": "Delete servers",
    "servers.transfer": "Transfer server ownership",
    # Security and moderation
    "automod.*": "Full AutoMod management",
    "automod.read": "View AutoMod rules and settings",
    "automod.edit": "Edit AutoMod rules",
    "reports.*": "Full report management",
    "reports.read": "View reports",
    "reports.action": "Take action on reports",
    "blocked_users.*": "Full user blocking management",
    "blocked_users.read": "View blocked users",
    "blocked_users.edit": "Block/unblock users",
    "blocked_hashes.*": "Full hash blocking management",
    "blocked_hashes.read": "View blocked hashes",
    "blocked_hashes.edit": "Block/unblock hashes",
    # System operations
    "metrics.read": "View system metrics and statistics",
    "config.read": "View system configuration",
    "config.edit": "Edit system configuration",
    "config.security": "Edit security-sensitive configuration",
    "logs.read": "View system logs",
    "maintenance": "Perform maintenance operations",
    # Tickets and support
    "tickets.*": "Full ticket management",
    "tickets.read": "View tickets",
    "tickets.respond": "Respond to tickets",
    "tickets.close": "Close tickets",
    # Admin management
    "admin.read": "View admin accounts and roles",
    "admin.edit": "Edit admin accounts",
    "admin.roles": "Manage admin roles and permissions",
    "admin.approvals": "Handle approval workflows",
    # Wildcard permissions
    "*": "Full system access (super admin only)",
}


# Actions that require approval when not single-admin
APPROVAL_REQUIRED_ACTIONS = {
    "users.force_purge": "Force purging user accounts",
    "users.delete": "Deleting user accounts",
    "servers.delete": "Deleting servers",
    "config.security": "Modifying security configuration",
    "admin.roles": "Modifying admin roles",
}


def check_permission(
    admin_id: int, required_permission: str, db, config: Optional[Dict[str, Any]] = None
) -> AdminPermission:
    """
    Check if an admin has a specific permission.

    Args:
        admin_id: The admin user ID
        required_permission: The permission scope to check (e.g., "users.read")
        db: Database instance
        config: Configuration dictionary (optional)

    Returns:
        AdminPermission with the check result
    """
    try:
        # Get admin's roles
        roles = _get_admin_roles(db, admin_id)

        if not roles:
            return AdminPermission(
                has_permission=False, reason="No roles assigned to admin"
            )

        # Check each role for the permission
        for role in roles:
            permissions = role.permissions

            # Check for wildcard permission (super admin)
            if permissions.get("*", False):
                return AdminPermission(has_permission=True)

            # Check for wildcard sub-permissions
            if "." in required_permission:
                scope_prefix = required_permission.split(".")[0] + ".*"
                if permissions.get(scope_prefix, False):
                    return AdminPermission(has_permission=True)

            # Check for exact permission
            if permissions.get(required_permission, False):
                return AdminPermission(has_permission=True)

        # Permission not found in any role
        return AdminPermission(
            has_permission=False,
            reason=f"Permission '{required_permission}' not granted in any assigned role",
        )

    except Exception as e:
        logger.error(f"Error checking permission for admin {admin_id}: {e}")
        return AdminPermission(
            has_permission=False, reason="Error checking permissions"
        )


def check_admin_permission(
    admin_id: int, required_permission: str, db, config: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Convenience wrapper for check_permission that returns a boolean.

    Args:
        admin_id: The admin user ID
        required_permission: The permission scope to check (e.g., "users.read")
        db: Database instance
        config: Configuration dictionary (optional)

    Returns:
        True if admin has permission, False otherwise
    """
    result = check_permission(admin_id, required_permission, db, config)
    return result.has_permission


def check_approval_required(
    admin_id: int, action: str, db, config: Optional[Dict[str, Any]] = None
) -> AdminPermission:
    """
    Check if an action requires approval and handle single-admin bypass.

    Args:
        admin_id: The admin user ID
        action: The action being performed
        db: Database instance
        config: Configuration dictionary

    Returns:
        AdminPermission with approval requirements
    """
    try:
        # Get admin config
        admin_config = config or {}
        approval_config = admin_config.get("approval_workflows", {})

        # Check if approval workflows are enabled
        if not approval_config.get("enabled", False):
            return AdminPermission(has_permission=True)

        # Check if single-admin bypass is enabled
        if approval_config.get("single_admin_bypass", True):
            # Count total admins
            admin_count = db.fetch_one("SELECT COUNT(*) as c FROM admin_users")
            if admin_count and admin_count.get("c", 0) == 1:
                # Only one admin - bypass approval
                return AdminPermission(has_permission=True)

        # Check if this action requires approval
        requires_approval = action in approval_config.get("require_approval_for", [])

        if requires_approval:
            # Check if admin has super admin permissions
            perm_result = check_permission(admin_id, "*", db, config)
            if perm_result.has_permission:
                # Super admins can bypass approval
                return AdminPermission(has_permission=True)

            # Create approval request
            approval_id = _create_approval_request(
                db, admin_id, action, approval_config
            )

            return AdminPermission(
                has_permission=False,
                requires_approval=True,
                approval_id=approval_id,
                reason=f"Action '{action}' requires approval from {approval_config.get('approval_required_admins', 2)} admins",
            )

        return AdminPermission(has_permission=True)

    except Exception as e:
        logger.error(f"Error checking approval requirements: {e}")
        return AdminPermission(
            has_permission=False, reason="Error checking approval requirements"
        )


def _get_admin_roles(db, admin_id: int) -> List[AdminRole]:
    """Get all roles assigned to an admin."""
    try:
        query = """
            SELECT r.id, r.name, r.description, r.permissions, r.created_at, r.created_by, r.is_system
            FROM admin_roles r
            JOIN admin_role_assignments a ON r.id = a.role_id
            WHERE a.admin_id = ?
        """
        rows = db.fetch_all(query, (admin_id,))

        roles = []
        for row in rows:
            if isinstance(row, dict):
                permissions = (
                    json.loads(row["permissions"])
                    if isinstance(row["permissions"], str)
                    else row["permissions"]
                )
                roles.append(
                    AdminRole(
                        id=row["id"],
                        name=row["name"],
                        description=row["description"],
                        permissions=permissions,
                        created_at=row["created_at"],
                        created_by=row["created_by"],
                        is_system=bool(row.get("is_system", False)),
                    )
                )
            else:
                # Handle tuple format
                permissions = json.loads(row[3]) if isinstance(row[3], str) else row[3]
                roles.append(
                    AdminRole(
                        id=row[0],
                        name=row[1],
                        description=row[2],
                        permissions=permissions,
                        created_at=row[4],
                        created_by=row[5],
                        is_system=bool(row[7]) if len(row) > 7 else False,
                    )
                )

        return roles
    except Exception as e:
        logger.error(f"Error getting admin roles for {admin_id}: {e}")
        return []


def _create_approval_request(
    db, requested_by: int, action_type: str, config: Dict[str, Any]
) -> int:
    """Create an approval request for an action."""
    try:
        now = int(time.time() * 1000)
        expires_hours = config.get("approval_timeout_hours", 48)
        expires_at = now + (expires_hours * 3600 * 1000)

        # Generate approval ID
        import src.utils.encryption as encryption

        approval_id = encryption.generate_snowflake_id()

        db.execute(
            """
            INSERT INTO admin_approvals 
            (id, requested_by, action_type, status, required_approvals, current_approvals, 
             expires_at, created_at, updated_at)
            VALUES (?, ?, ?, 'pending', ?, 0, ?, ?, ?)
        """,
            (
                approval_id,
                requested_by,
                action_type,
                config.get("approval_required_admins", 2),
                expires_at,
                now,
                now,
            ),
        )

        logger.info(
            f"Created approval request {approval_id} for action {action_type} by admin {requested_by}"
        )
        return approval_id

    except Exception as e:
        logger.error(f"Error creating approval request: {e}")
        raise


def log_admin_action(
    db,
    admin_id: int,
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[int] = None,
    details: Optional[Union[str, Dict[str, Any]]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    status: str = "success",
) -> bool:
    """
    Log an admin action to the audit log with dual file/database logging.

    Args:
        db: Database instance
        admin_id: The admin performing the action
        action: The action being performed
        target_type: Type of target (user, server, config, etc.)
        target_id: ID of the target
        details: Additional details about the action
        ip_address: IP address of the admin
        user_agent: User agent string
        status: Status of the action (success, failed, pending_approval)

    Returns:
        True if logged successfully, False otherwise
    """
    try:
        # Check if dual logging is enabled in config
        import utils.config as config

        admin_config = config.get("admin_ui", {})
        audit_config = admin_config.get("audit", {})

        log_to_file = audit_config.get("log_to_file", True)
        log_to_db = audit_config.get("log_to_database", True)

        # For sensitive actions, always log to database regardless of config
        sensitive_actions = [
            "users.force_purge",
            "users.delete",
            "servers.delete",
            "config.modify",
        ]
        if action in sensitive_actions:
            log_to_db = True

        # Use AdminLogger for dual logging
        from src.core.admin.logging import get_admin_logger, AdminLogEntry

        admin_logger = get_admin_logger()

        # Convert details to string for logging
        if isinstance(details, dict):
            details_str = json.dumps(details)
            metadata = details
        elif isinstance(details, str):
            details_str = details
            metadata = {"message": details}
        else:
            details_str = None
            metadata = None

        entry = AdminLogEntry(
            admin_id=admin_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details_str,
            ip_address=ip_address,
            user_agent=user_agent,
            status=status,
            metadata=metadata,
        )

        success = admin_logger.log_action(
            db, entry, log_to_file=log_to_file, log_to_db=log_to_db
        )

        if success:
            logger.debug(f"Logged admin action {action} by admin {admin_id}")
        else:
            logger.warning(f"Failed to log admin action {action} by admin {admin_id}")

        return success

    except Exception as e:
        logger.error(f"Error logging admin action: {e}")
        return False


def get_admin_permissions_summary(db, admin_id: int) -> Dict[str, Any]:
    """Get a summary of an admin's permissions and roles."""
    try:
        roles = _get_admin_roles(db, admin_id)

        # Collect all permissions from roles
        all_permissions = {}
        for role in roles:
            for perm, granted in role.permissions.items():
                if granted:
                    all_permissions[perm] = True

        # Check for wildcard
        has_wildcard = all_permissions.get("*", False)

        return {
            "admin_id": admin_id,
            "roles": [
                {"id": r.id, "name": r.name, "description": r.description}
                for r in roles
            ],
            "permissions": all_permissions,
            "has_wildcard": has_wildcard,
            "is_super_admin": has_wildcard,
        }

    except Exception as e:
        logger.error(f"Error getting admin permissions summary: {e}")
        return {
            "admin_id": admin_id,
            "roles": [],
            "permissions": {},
            "has_wildcard": False,
            "is_super_admin": False,
        }
