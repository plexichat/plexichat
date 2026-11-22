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

from .request_roles import (
    cached_session_roles,
    cached_parsed_session_roles,
    remember_parsed_session_roles,
    clear_session_roles,
    current_session_credential,
)
from .permission_cache import (
    get_cached_max_position,
    invalidate_position_cache,
)
from .permission_grant_cache import (
    get_cached_grants,
    invalidate_grant_cache,
)


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
    "users.storage": "Manage per-user storage quotas",
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
    # Application / bot oversight
    "admin.bots.read": "View system-wide application and bot oversight data",
    # Data export (DSAR)
    "data_export.read": "View and manage data export requests",
    "data_export.process": "Approve, deny, and generate data exports",
    # Admin audit trail
    "admin.audit.read": "View and export the admin audit log",
    # Database migrations
    "admin.migrations.read": "View migration status, metadata, and execution logs",
    "admin.migrations.execute": "Run, apply, and roll back database migrations",
    # Admin security operations
    "admin.security.ipblock": "Block and unblock IP addresses platform-wide",
    "admin.security.tokens": "Create, rotate, and revoke permanent API access tokens",
    "admin.security.sessions": "Force logout and lock/unlock user accounts",
    "admin.security.logout_all": "Invalidate every user session platform-wide",
    "admin.security.usernames": "Manage banned username patterns",
    # Platform-wide moderation
    "admin.moderation.bans": "Ban and unban users platform-wide",
    "admin.moderation.hashes": "Write and remove blocked content hashes",
    "admin.moderation.rules": "Create, update, and delete AutoMod rules on any server",
    "admin.moderation.config": "Modify the global AutoMod configuration",
    # Bulk data operations
    "admin.data.reindex": "Rebuild the search index (bulk message decryption)",
    # Artifacts
    "admin.artifacts.manage": "Force-delete artifacts and manage retention policies",
    # Dashboard runtime tuning
    "admin.dashboard.tuning": "Hot-reload cross-worker poll-loop tuning constants",
    # Licensing / federation / telemetry
    "admin.license.manage": "Apply and force-revalidate licenses",
    "admin.plexijoin.manage": "Establish and sever cross-instance federation links",
    "admin.telemetry.reset": "Reset collected telemetry statistics",
    # Wildcard permissions
    "*": "Full system access (super admin only)",
}


def _build_delegable_permissions() -> frozenset:
    """Derive the closed vocabulary of keys a role request may contain.

    ``PERMISSION_SCOPES`` doubles as the documentation of wildcard scopes
    (``"*"`` and ``"scope.*"``).  Those are reserved for seeded system roles:
    the permission check expands them for the holder, but they must never be
    accepted from a role create/update payload.  Everything else in the table
    is an explicit, delegable leaf scope.
    """
    return frozenset(
        key for key in PERMISSION_SCOPES if key != "*" and not key.endswith(".*")
    )


DELEGABLE_PERMISSIONS = _build_delegable_permissions()


def validate_delegable_permissions(permissions: Any) -> Dict[str, bool]:
    """Validate a permission map submitted by a role-management request.

    This is the single choke point for the closed vocabulary.  It rejects
    wildcards, unknown keys, non-string keys and non-boolean values, so a
    caller cannot smuggle a scope that was never declared in
    :data:`PERMISSION_SCOPES` into a role.

    Args:
        permissions: The raw ``permissions`` mapping from the request body.

    Returns:
        A plain ``dict`` copy containing only validated leaf scopes.

    Raises:
        ValueError: If any key or value is not delegable.
    """
    if not isinstance(permissions, dict):
        raise ValueError("permissions must be an object")
    validated: Dict[str, bool] = {}
    for name, granted in permissions.items():
        if not isinstance(name, str):
            raise ValueError("permission keys must be strings")
        if name == "*" or name.endswith(".*"):
            raise ValueError("wildcard permissions cannot be delegated")
        if name not in DELEGABLE_PERMISSIONS:
            raise ValueError(f"unknown permission scope: {name}")
        if not isinstance(granted, bool):
            raise ValueError("permission values must be booleans")
        validated[name] = granted
    return validated


# Actions that require approval when not single-admin
APPROVAL_REQUIRED_ACTIONS = {
    "users.force_purge": "Force purging user accounts",
    "users.delete": "Deleting user accounts",
    "servers.delete": "Deleting servers",
    "config.security": "Modifying security configuration",
    "admin.roles": "Modifying admin roles",
    # Destructive migration execution
    "migrations.run": "Running a database migration",
    "migrations.rollback": "Rolling back a database migration",
    "migrations.apply_all": "Applying all pending database migrations",
    "migrations.emergency_override": "Minting an emergency migration delay-bypass token",
    # Bulk data exposure / destruction
    "search.reindex": "Full search reindex (mass message decryption)",
    "artifacts.force_delete": "Force-deleting artifacts across every server",
}


def is_approval_required_action(
    action: str, approval_config: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Check whether an action requires approval.

    ``APPROVAL_REQUIRED_ACTIONS`` is the default (canonical) set. Operators
    may extend — but never shrink — it via the
    ``approval_workflows.require_approval_for`` config list.

    Args:
        action: The action being performed
        approval_config: The ``approval_workflows`` config mapping (optional)

    Returns:
        True if the action requires approval, False otherwise
    """
    if action in APPROVAL_REQUIRED_ACTIONS:
        return True
    configured = (approval_config or {}).get("require_approval_for", []) or []
    return action in set(configured)


def _parse_permission_payload(raw) -> Optional[Dict[str, bool]]:
    """Parse a stored ``admin_roles.permissions`` value into a dict.

    Malformed or non-dict payloads are unusable and fail closed (they grant
    nothing), matching how a broken row behaved through ``_get_admin_roles``.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _is_granted(value) -> bool:
    """Only an explicit JSON boolean ``true`` grants a permission scope.

    Role payloads come from a JSON column and may have been written by a
    legacy importer, a migration, or a manual DB fix that never went through
    :func:`validate_delegable_permissions`. Treating any truthy value as a
    grant lets junk such as ``"false"``, ``1`` or ``[]`` silently escalate a
    role. Both the cached and the fresh permission paths must fail closed on
    anything that is not exactly ``True``.
    """
    return value is True


def _payloads_grant(payloads, required_permission: str) -> bool:
    """Pure form of the wildcard/exact grant check shared by both read paths.

    This reproduces the three checks in ``check_permission`` verbatim: a
    super-admin ``*``, a wildcard ``scope.*`` prefix, then the exact scope.
    """
    for permissions in payloads:
        if not isinstance(permissions, dict):
            continue
        if _is_granted(permissions.get("*")):
            return True
        if "." in required_permission:
            scope_prefix = required_permission.split(".")[0] + ".*"
            if _is_granted(permissions.get(scope_prefix)):
                return True
        if _is_granted(permissions.get(required_permission)):
            return True
    return False


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
        # Fast path: when this request validated a session, the role payloads
        # arrived with it in the session query. Reuse them instead of issuing a
        # second round trip. No value (self-test identity path, direct callers,
        # or a different admin/db) simply takes the original query below.
        cached = cached_session_roles(db, admin_id)
        if cached is not None:
            # The raw blobs are fixed for the request, but JSON-decoding them
            # on every gate was pure repeated work (maintenance routes stack
            # two scopes). Decode once per request and reuse the dicts across
            # subsequent gates; the memo uses the same identity guard as the
            # raw cache and is cleared on any privilege mutation.
            payloads = cached_parsed_session_roles(db, admin_id)
            if payloads is None:
                payloads = tuple(
                    parsed
                    for parsed in (_parse_permission_payload(raw) for raw in cached)
                    if parsed is not None
                )
                remember_parsed_session_roles(db, admin_id, payloads)
            if not payloads:
                return AdminPermission(
                    has_permission=False, reason="No roles assigned to admin"
                )
            if _payloads_grant(payloads, required_permission):
                return AdminPermission(has_permission=True)
            return AdminPermission(
                has_permission=False,
                reason=f"Permission '{required_permission}' not granted in any assigned role",
            )

        # Get admin's roles
        roles = _get_admin_roles(db, admin_id)

        if not roles:
            return AdminPermission(
                has_permission=False, reason="No roles assigned to admin"
            )

        # Check each role for the permission
        if _payloads_grant((role.permissions for role in roles), required_permission):
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


def get_admin_granted_permissions(db, admin_id: int) -> frozenset:
    """Return the set of permission scopes granted to an admin by its roles.

    Wildcard keys are returned verbatim (``"*"``, ``"scope.*"``); callers are
    responsible for expanding them.  The lookup is memoised in a bounded,
    short-TTL store keyed by ``(db, credential, admin_id)``: a privileged
    request resolves the same role set for the permission gate, the delegation
    check and the hierarchy helper, so those collapse to one database read.
    Every privilege mutation clears the store (see
    :func:`invalidate_admin_permission_caches`), so a revoked scope is gone on
    the very next check.  The loader fails closed -- a query error or a broken
    role row grants nothing and is never cached as a grant.
    """

    def _load() -> frozenset:
        granted = set()
        try:
            roles = _get_admin_roles(db, admin_id)
        except Exception as e:
            # Fail closed: a database/parse error must never be interpreted as
            # a grant, cached or otherwise.
            logger.error(f"Error resolving granted permissions for {admin_id}: {e}")
            return frozenset()
        for role in roles:
            permissions = role.permissions
            if not isinstance(permissions, dict):
                continue
            for permission, allowed in permissions.items():
                if _is_granted(allowed):
                    granted.add(permission)
        return frozenset(granted)

    return get_cached_grants(db, current_session_credential(), admin_id, _load)


def can_delegate_permissions(db, admin_id: int, permissions: Dict[str, bool]) -> bool:
    """Enforce the scope hierarchy for role creation/updates.

    An admin may only write scopes it already holds itself (directly or via a
    wildcard).  Without this, a lower-position admin could mint a role with
    privileges above its own and then assign it to itself or a peer, escaping
    the position hierarchy.  A holder of ``*`` may delegate any leaf scope.
    """
    held = get_admin_granted_permissions(db, admin_id)
    if "*" in held:
        return True
    for permission, allowed in permissions.items():
        if not allowed:
            continue
        if permission in held:
            continue
        scope_prefix = permission.split(".", 1)[0] + ".*"
        if scope_prefix in held:
            continue
        return False
    return True


def can_grant_existing_role(db, admin_id: int, role_id: int, *, _out=None) -> bool:
    """Object-level authorization for assigning a concrete role object.

    Route-level scopes and the position hierarchy answer "may this admin
    manage roles at all".  This answers the object-level question "may this
    admin hand out *this* role": a role that grants scopes the actor does not
    hold must not become a privilege-laundering path, even when its position
    sits below the actor's.

    Returns False when the role cannot be read or its permission payload is
    malformed, so a broken row can never fail open.

    ``_out`` is an optional dict. When supplied, the exact stored permission
    payload read here is written to ``_out['permissions']`` *from the same read
    that decides authorization*. Callers that go on to write a role assignment
    bind that write to this snapshot (compare-and-swap) so a concurrent widening
    of the role's scopes between the check and the write cannot escalate.
    """
    row = db.fetch_one("SELECT permissions FROM admin_roles WHERE id = ?", (role_id,))
    if not row:
        return False
    raw = row["permissions"] if isinstance(row, dict) else row[0]
    if _out is not None:
        _out["permissions"] = raw
    try:
        permissions = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return False
    if not isinstance(permissions, dict):
        return False
    return can_delegate_permissions(db, admin_id, permissions)


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

        # Check if this action requires approval. The canonical default set
        # (APPROVAL_REQUIRED_ACTIONS) always applies; config may add more.
        requires_approval = is_approval_required_action(action, approval_config)

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
                        is_system=bool(row[6]) if len(row) > 6 else False,
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


class _PositionLookupFailed(int):
    """A 0-valued position meaning "lookup failed", not "no roles".

    Keeping it an ``int`` preserves the public ``get_admin_max_position``
    contract (0 on error) for best-effort callers, while hierarchy gates can
    distinguish an unreadable position from a genuinely role-less admin.
    """


def _load_admin_max_position(db, admin_id: int) -> int:
    """Single uncached role-position read; propagates DB errors to the caller."""
    row = db.fetch_one(
        """
        SELECT COALESCE(MAX(r.position), 0) as max_pos
        FROM admin_roles r
        JOIN admin_role_assignments a ON r.id = a.role_id
        WHERE a.admin_id = ?
    """,
        (admin_id,),
    )
    if row:
        return row["max_pos"] if isinstance(row, dict) else int(row[0])
    return 0


def get_admin_max_position(db, admin_id: int) -> int:
    """
    Get the highest position of all roles assigned to an admin.

    The lookup is memoised for a short, bounded window because the hierarchy
    checks (``can_manage_admin`` / ``can_manage_role``) resolve the same actor
    repeatedly within a request. Every privilege mutation clears the cache via
    :func:`invalidate_admin_permission_caches`, so a revoked/demoted role is
    reflected on the very next check. A failed lookup is not cached and yields
    a 0-valued :class:`_PositionLookupFailed` sentinel so hierarchy gates can
    deny instead of reading it as "no roles".

    Args:
        db: Database instance
        admin_id: The admin user ID

    Returns:
        The maximum position value across all assigned roles (0 if no roles)
    """
    try:
        return get_cached_max_position(
            db, admin_id, lambda: _load_admin_max_position(db, admin_id)
        )
    except Exception as e:
        logger.error(f"Error getting admin max position: {e}")
        return _PositionLookupFailed(0)


def can_manage_admin(db, current_admin_id: int, target_admin_id: int) -> bool:
    """
    Check if the current admin can manage (modify roles, toggle status, delete) a target admin.

    Hierarchy rule: An admin can only manage another admin whose highest role position
    is strictly lower than their own highest role position.

    Args:
        db: Database instance
        current_admin_id: The admin performing the action
        target_admin_id: The admin being acted upon

    Returns:
        True if the current admin can manage the target admin
    """
    # Prevent self-management
    if current_admin_id == target_admin_id:
        logger.warning(f"Admin {current_admin_id} attempted to manage themselves")
        return False

    # Get both admins' max positions. A failed lookup must never be read as
    # "no roles" (0): the target's real position may be higher than the
    # actor's, so treating an error as 0 would grant authority over an admin
    # the actor cannot legitimately manage. The strict comparison itself is
    # unchanged; only an unreadable position forces a deny.
    current_pos = get_admin_max_position(db, current_admin_id)
    target_pos = get_admin_max_position(db, target_admin_id)
    if isinstance(current_pos, _PositionLookupFailed) or isinstance(
        target_pos, _PositionLookupFailed
    ):
        logger.error(
            f"Error checking admin hierarchy for {current_admin_id} -> "
            f"{target_admin_id}: position lookup failed"
        )
        return False

    # Can only manage if current admin's position is strictly higher
    return current_pos > target_pos


def can_manage_role(db, admin_id: int, role_id: int) -> bool:
    """
    Check if an admin can manage (modify, delete) a specific role.

    Hierarchy rule: An admin can only manage roles at a position strictly lower
    than their own highest role position.

    Args:
        db: Database instance
        admin_id: The admin user ID
        role_id: The role ID to check

    Returns:
        True if the admin can manage the role
    """
    admin_pos = get_admin_max_position(db, admin_id)

    role = db.fetch_one(
        "SELECT position, is_system FROM admin_roles WHERE id = ?", (role_id,)
    )
    if not role:
        return False

    role_pos = role["position"] if isinstance(role, dict) else int(role[0])
    is_system = role.get("is_system", 0) if isinstance(role, dict) else bool(role[1])

    if is_system:
        # System roles can only be managed by super admins (position >= 100)
        return admin_pos >= 100

    return admin_pos > role_pos


# Action identifiers that mutate a principal's privileges or account state.
# These are the *route-level* action names the admin handlers pass to
# ``log_admin_action`` (e.g. ``role.update`` / ``role.assign``), not permission
# scope names.  The set is a single source of truth for two coupled decisions:
# the audit log must always reach the database for these actions, AND the
# request-scoped role payloads captured at session validation are stale the
# moment one of them succeeds and must be discarded.  Keeping them together
# guarantees a newly added privileged route cannot update one without the
# other.
PRIVILEGE_MUTATING_ACTIONS = frozenset(
    {
        # Legacy/direct callers that still use scope-style identifiers.
        "users.force_purge",
        "users.delete",
        "servers.delete",
        "config.modify",
        "config.security",
        "config.edit",
        # Admin-account lifecycle and credential changes.
        "admin_user.create",
        "admin_user.update",
        "admin_user.delete",
        "admin_user.toggle_status",
        "force_password_change",
        # Role/privilege changes.
        "role.create",
        "role.update",
        "role.delete",
        "role.assign",
        "role.revoke",
        # Approval decisions (can grant the underlying privilege).
        "approval.approve",
        "approval.reject",
        "approval.cancel",
    }
)


def invalidate_admin_permission_caches() -> None:
    """Drop authorization state that a privilege mutation just invalidated.

    ``src.core.auth.privilege_cache.invalidate_privilege_caches`` clears the
    memoised *user* auth context (``token_verify:*``, ``user_data:*`` ...) but
    never touches the admin-side authorization caches:

    * the request-scoped role payloads that :func:`check_permission` reuses via
      ``cached_session_roles``; and
    * the bounded, short-TTL role-position cache behind
      :func:`get_admin_max_position` (used by the hierarchy gates); and
    * the bounded ``(db, credential, admin)`` role-grant cache behind
      :func:`get_admin_granted_permissions` (used by the delegation checks).

    Without this call, a role edit / assignment / revocation followed by any
    further authorization check in the same request would be decided against
    the pre-mutation permission set -- a just-revoked scope still appears
    granted, and a demoted admin keeps its old hierarchy position.

    Call this from the mutation point after the database write succeeds.  It is
    safe to call more than once and is a no-op when no state was cached.
    """
    clear_session_roles()
    invalidate_position_cache()
    invalidate_grant_cache()


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

        # Privileged mutations must always reach the database audit table,
        # even when an operator has disabled DB logging in configuration
        # (file-only logging is not queryable through the audit UI and can be
        # rotated away). See ``PRIVILEGE_MUTATING_ACTIONS`` for the rationale
        # and the shared action-identifier vocabulary.
        if action in PRIVILEGE_MUTATING_ACTIONS:
            log_to_db = True
            # The mutation just changed the effective role/permission set. The
            # request-scoped payloads captured at session validation are now
            # stale, so drop them before doing anything else. This runs even
            # if file/DB logging below fails: a subsequent check_permission in
            # this request must never authorise against the pre-mutation roles
            # (e.g. a revoked scope still appearing granted). The routes'
            # invalidate_privilege_caches() call only clears user auth caches,
            # not this request-local one.
            invalidate_admin_permission_caches()

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
            if not isinstance(role.permissions, dict):
                continue
            for perm, granted in role.permissions.items():
                if _is_granted(granted):
                    all_permissions[perm] = True

        # Check for wildcard
        has_wildcard = all_permissions.get("*", False)

        return {
            "admin_id": str(admin_id),
            "roles": [
                {"id": str(r.id), "name": r.name, "description": r.description}
                for r in roles
            ],
            "permissions": all_permissions,
            "has_wildcard": has_wildcard,
            "is_super_admin": has_wildcard,
        }

    except Exception as e:
        logger.error(f"Error getting admin permissions summary: {e}")
        return {
            "admin_id": str(admin_id),
            "roles": [],
            "permissions": {},
            "has_wildcard": False,
            "is_super_admin": False,
        }
