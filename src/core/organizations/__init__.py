"""
Organizations Module - Manages organization IDs, members, and invites.

This module provides:
- Organization creation and management
- Member management (add, remove, roles)
- Invite system (2-step flow for existing users, direct for new)
- Managed settings (org-locked user settings)
- Server restrictions (allowlist/blocklist)

Usage:
    from src.core import organizations
    organizations.setup(db, auth)
    
    # Create org (requires can_create_org feature)
    org = organizations.create_org("acme", "Acme Corp", root_user_id)
    
    # Invite existing user
    invite = organizations.create_invite(org.id, root_user_id, "existing", "username")
    
    # User accepts, root approves
    organizations.accept_invite(invite.id, user_id)
    organizations.approve_invite(invite.id, root_user_id)
"""

import json
import time
import secrets
from typing import Optional, List, Dict, Any

import utils.logger as logger
import utils.config as config

from .schema import create_tables, add_org_columns_to_users
from .models import Organization, OrgMember, OrgInvite, OrgManagedSetting, OrgRole
from .exceptions import (
    OrganizationError, OrgNotFoundError, OrgExistsError,
    MemberNotFoundError, InviteNotFoundError, InviteExpiredError,
    PermissionDeniedError, FeatureRequiredError
)

_db = None
_auth = None
_setup_complete = False


def setup(db, auth_module=None) -> None:
    """Initialize the organizations module."""
    global _db, _auth, _setup_complete
    
    _db = db
    _auth = auth_module
    create_tables(db)
    add_org_columns_to_users(db)
    _ensure_default_org()
    _setup_complete = True
    logger.info("Organizations module initialized")


def is_setup() -> bool:
    """Check if module is initialized."""
    return _setup_complete


def _get_db():
    """Get database instance."""
    if not _setup_complete:
        raise RuntimeError("Organizations module not initialized. Call organizations.setup(db) first.")
    return _db


def _get_config(key: str, default: Any = None) -> Any:
    """Get organizations configuration value."""
    org_config = config.get("organizations", {})
    keys = key.split(".")
    value = org_config
    for k in keys:
        if isinstance(value, dict):
            value = value.get(k, default)
        else:
            return default
    return value if value is not None else default


def _generate_invite_code(org_name: str, is_registration: bool = False) -> str:
    """Generate an invite code."""
    code_length = _get_config("invites.code_length", 32)
    random_part = secrets.token_hex(code_length // 2)
    if is_registration:
        return f"ORG-{org_name[:10]}-{random_part[:8]}-{random_part[8:16]}-{random_part[16:24]}"
    return random_part


def _ensure_default_org() -> None:
    """Ensure default organization exists."""
    db = _get_db()
    
    default_name = _get_config("default_org_name", "default")
    default_display = _get_config("default_org_display_name", "PlexiChat")
    
    existing = db.fetch_one("SELECT id FROM organizations WHERE name = ?", (default_name,))
    if existing:
        return
    
    # Find admin user (first user or user with admin permissions)
    admin_row = db.fetch_one(
        "SELECT id FROM auth_users ORDER BY created_at ASC LIMIT 1"
    )
    root_user_id = admin_row["id"] if admin_row else 1
    
    from src.utils.encryption import generate_snowflake_id
    org_id = generate_snowflake_id()
    now = int(time.time())
    
    db.execute(
        """INSERT INTO organizations 
           (id, name, display_name, root_user_id, is_default, created_at, updated_at)
           VALUES (?, ?, ?, ?, 1, ?, ?)""",
        (org_id, default_name, default_display, root_user_id, now, now)
    )
    
    logger.info(f"Created default organization '{default_name}' with ID {org_id}")


# === Organization Management ===

def create_org(name: str, display_name: str, root_user_id: int) -> Organization:
    """
    Create a new organization.
    
    Requires the root user to have 'can_create_org' feature flag.
    If require_otp is enabled, root must have 2FA enabled.
    """
    db = _get_db()
    
    # Check feature flag
    try:
        from src.core import features
        if features.is_setup() and not features.has_feature(root_user_id, "can_create_org"):
            raise FeatureRequiredError("User does not have 'can_create_org' feature")
    except ImportError:
        pass
    
    # Check 2FA requirement
    if _get_config("root_user.require_otp", True) and _auth:
        user = _auth.get_user(root_user_id)
        if user and not getattr(user, "totp_enabled", False):
            raise PermissionDeniedError("2FA must be enabled to create an organization")
    
    # Check if org name exists
    existing = db.fetch_one("SELECT id FROM organizations WHERE name = ?", (name.lower(),))
    if existing:
        raise OrgExistsError(f"Organization '{name}' already exists")
    
    from src.utils.encryption import generate_snowflake_id
    org_id = generate_snowflake_id()
    now = int(time.time())
    
    db.execute(
        """INSERT INTO organizations 
           (id, name, display_name, root_user_id, is_default, created_at, updated_at)
           VALUES (?, ?, ?, ?, 0, ?, ?)""",
        (org_id, name.lower(), display_name, root_user_id, now, now)
    )
    
    # Add root user as member with 'root' role
    member_id = generate_snowflake_id()
    db.execute(
        """INSERT INTO org_members (id, org_id, user_id, role, joined_at)
           VALUES (?, ?, ?, 'root', ?)""",
        (member_id, org_id, root_user_id, now)
    )
    
    # Update user's org_id
    db.execute("UPDATE auth_users SET org_id = ? WHERE id = ?", (org_id, root_user_id))
    
    # Add org_root badge
    try:
        from src.core import features
        if features.is_setup():
            features.add_badge(root_user_id, root_user_id, "org_root")
    except Exception:
        pass
    
    logger.info(f"Created organization '{name}' (ID: {org_id}) with root user {root_user_id}")
    
    return get_org(org_id)


def get_org(org_id: int) -> Optional[Organization]:
    """Get organization by ID."""
    db = _get_db()
    
    row = db.fetch_one(
        """SELECT id, name, display_name, root_user_id, is_default, created_at, updated_at,
                  settings, default_servers, allowed_servers, blocked_servers,
                  allow_invites, invite_requires_approval
           FROM organizations WHERE id = ?""",
        (org_id,)
    )
    
    if not row:
        return None
    
    return _row_to_org(row)


def get_org_by_name(name: str) -> Optional[Organization]:
    """Get organization by name."""
    db = _get_db()
    
    row = db.fetch_one(
        """SELECT id, name, display_name, root_user_id, is_default, created_at, updated_at,
                  settings, default_servers, allowed_servers, blocked_servers,
                  allow_invites, invite_requires_approval
           FROM organizations WHERE name = ?""",
        (name.lower(),)
    )
    
    if not row:
        return None
    
    return _row_to_org(row)


def get_default_org() -> Organization:
    """Get the default organization."""
    db = _get_db()
    
    row = db.fetch_one(
        """SELECT id, name, display_name, root_user_id, is_default, created_at, updated_at,
                  settings, default_servers, allowed_servers, blocked_servers,
                  allow_invites, invite_requires_approval
           FROM organizations WHERE is_default = 1"""
    )
    
    if not row:
        _ensure_default_org()
        return get_default_org()
    
    return _row_to_org(row)


def _row_to_org(row) -> Organization:
    """Convert database row to Organization object."""
    return Organization(
        id=row["id"],
        name=row["name"],
        display_name=row["display_name"],
        root_user_id=row["root_user_id"],
        is_default=bool(row["is_default"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        settings=json.loads(row["settings"]) if row["settings"] else {},
        default_servers=json.loads(row["default_servers"]) if row["default_servers"] else [],
        allowed_servers=json.loads(row["allowed_servers"]) if row["allowed_servers"] else None,
        blocked_servers=json.loads(row["blocked_servers"]) if row["blocked_servers"] else [],
        allow_invites=bool(row["allow_invites"]),
        invite_requires_approval=bool(row["invite_requires_approval"])
    )


# === Member Management ===

def get_user_org(user_id: int) -> Optional[Organization]:
    """Get the organization a user belongs to."""
    db = _get_db()
    
    row = db.fetch_one("SELECT org_id FROM auth_users WHERE id = ?", (user_id,))
    if not row or not row["org_id"]:
        return get_default_org()
    
    return get_org(row["org_id"])


def get_org_members(org_id: int) -> List[OrgMember]:
    """Get all members of an organization."""
    db = _get_db()
    
    rows = db.fetch_all(
        """SELECT m.id, m.org_id, m.user_id, m.role, m.joined_at, m.invited_by,
                  u.username
           FROM org_members m
           JOIN auth_users u ON m.user_id = u.id
           WHERE m.org_id = ?
           ORDER BY m.joined_at ASC""",
        (org_id,)
    )
    
    return [
        OrgMember(
            id=row["id"],
            org_id=row["org_id"],
            user_id=row["user_id"],
            role=OrgRole(row["role"]),
            joined_at=row["joined_at"],
            invited_by=row["invited_by"],
            username=row["username"]
        )
        for row in rows
    ]


def get_member(org_id: int, user_id: int) -> Optional[OrgMember]:
    """Get a specific member of an organization."""
    db = _get_db()
    
    row = db.fetch_one(
        """SELECT m.id, m.org_id, m.user_id, m.role, m.joined_at, m.invited_by,
                  u.username
           FROM org_members m
           JOIN auth_users u ON m.user_id = u.id
           WHERE m.org_id = ? AND m.user_id = ?""",
        (org_id, user_id)
    )
    
    if not row:
        return None
    
    return OrgMember(
        id=row["id"],
        org_id=row["org_id"],
        user_id=row["user_id"],
        role=OrgRole(row["role"]),
        joined_at=row["joined_at"],
        invited_by=row["invited_by"],
        username=row["username"]
    )


def add_member(org_id: int, user_id: int, role: str = "member", invited_by: int = None) -> OrgMember:
    """Add a user to an organization."""
    db = _get_db()
    
    org = get_org(org_id)
    if not org:
        raise OrgNotFoundError(f"Organization {org_id} not found")
    
    # Check if already a member
    existing = get_member(org_id, user_id)
    if existing:
        return existing
    
    from src.utils.encryption import generate_snowflake_id
    member_id = generate_snowflake_id()
    now = int(time.time())
    
    db.execute(
        """INSERT INTO org_members (id, org_id, user_id, role, joined_at, invited_by)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (member_id, org_id, user_id, role, now, invited_by)
    )
    
    # Update user's org_id
    db.execute("UPDATE auth_users SET org_id = ? WHERE id = ?", (org_id, user_id))
    
    logger.info(f"Added user {user_id} to organization {org_id} with role '{role}'")
    
    return get_member(org_id, user_id)


def remove_member(org_id: int, user_id: int, disinherit: bool = True) -> bool:
    """
    Remove a user from an organization.
    
    If disinherit=True, moves user to default org.
    """
    db = _get_db()
    
    org = get_org(org_id)
    if not org:
        raise OrgNotFoundError(f"Organization {org_id} not found")
    
    # Cannot remove root user
    if org.root_user_id == user_id:
        raise PermissionDeniedError("Cannot remove root user from organization")
    
    member = get_member(org_id, user_id)
    if not member:
        return False
    
    db.execute("DELETE FROM org_members WHERE org_id = ? AND user_id = ?", (org_id, user_id))
    
    if disinherit:
        default_org = get_default_org()
        add_member(default_org.id, user_id, "member", org.root_user_id)
    else:
        db.execute("UPDATE auth_users SET org_id = NULL WHERE id = ?", (user_id,))
    
    logger.info(f"Removed user {user_id} from organization {org_id}")
    
    return True


def is_org_root(org_id: int, user_id: int) -> bool:
    """Check if user is the root of an organization."""
    org = get_org(org_id)
    return org and org.root_user_id == user_id


# === Invite Management ===

def create_invite(
    org_id: int,
    created_by: int,
    invite_type: str = "existing",
    target_username: str = None,
    expires_hours: int = None
) -> OrgInvite:
    """
    Create an organization invite.
    
    invite_type: "existing" (for existing users) or "registration" (for new users)
    """
    db = _get_db()
    
    org = get_org(org_id)
    if not org:
        raise OrgNotFoundError(f"Organization {org_id} not found")
    
    if not is_org_root(org_id, created_by):
        raise PermissionDeniedError("Only org root can create invites")
    
    if not org.allow_invites:
        raise PermissionDeniedError("Invites are disabled for this organization")
    
    # For existing user invites, verify target exists
    target_user_id = None
    if invite_type == "existing" and target_username:
        if _auth:
            target_user = _auth.get_user_by_username(target_username)
            if not target_user:
                raise MemberNotFoundError(f"User '{target_username}' not found")
            target_user_id = target_user.id
    
    from src.utils.encryption import generate_snowflake_id
    invite_id = generate_snowflake_id()
    code = _generate_invite_code(org.name, invite_type == "registration")
    now = int(time.time())
    
    if expires_hours is None:
        expires_hours = _get_config("invites.default_expiry_hours", 168)
    expires_at = now + (expires_hours * 3600) if expires_hours > 0 else None
    
    db.execute(
        """INSERT INTO org_invites 
           (id, org_id, code, invite_type, target_username, target_user_id, created_by, 
            created_at, expires_at, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
        (invite_id, org_id, code, invite_type, target_username, target_user_id, created_by, now, expires_at)
    )
    
    logger.info(f"Created {invite_type} invite for org {org_id} by user {created_by}")
    
    return get_invite(invite_id)


def get_invite(invite_id: int) -> Optional[OrgInvite]:
    """Get invite by ID."""
    db = _get_db()
    
    row = db.fetch_one(
        """SELECT id, org_id, code, invite_type, target_username, target_user_id,
                  created_by, created_at, expires_at, max_uses, uses, status,
                  user_accepted, user_accepted_at, root_approved, root_approved_at
           FROM org_invites WHERE id = ?""",
        (invite_id,)
    )
    
    if not row:
        return None
    
    return _row_to_invite(row)


def get_invite_by_code(code: str) -> Optional[OrgInvite]:
    """Get invite by code."""
    db = _get_db()
    
    row = db.fetch_one(
        """SELECT id, org_id, code, invite_type, target_username, target_user_id,
                  created_by, created_at, expires_at, max_uses, uses, status,
                  user_accepted, user_accepted_at, root_approved, root_approved_at
           FROM org_invites WHERE code = ?""",
        (code,)
    )
    
    if not row:
        return None
    
    return _row_to_invite(row)


def _row_to_invite(row) -> OrgInvite:
    """Convert database row to OrgInvite object."""
    return OrgInvite(
        id=row["id"],
        org_id=row["org_id"],
        code=row["code"],
        invite_type=row["invite_type"],
        target_username=row["target_username"],
        target_user_id=row.get("target_user_id"),
        created_by=row["created_by"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        max_uses=row["max_uses"],
        uses=row["uses"],
        status=row["status"],
        user_accepted=bool(row["user_accepted"]),
        user_accepted_at=row["user_accepted_at"],
        root_approved=bool(row["root_approved"]),
        root_approved_at=row["root_approved_at"]
    )


def get_org_invites(org_id: int, status_filter: str = None) -> List[OrgInvite]:
    """Get all invites for an organization."""
    db = _get_db()
    
    if status_filter:
        rows = db.fetch_all(
            """SELECT id, org_id, code, invite_type, target_username, target_user_id,
                      created_by, created_at, expires_at, max_uses, uses, status,
                      user_accepted, user_accepted_at, root_approved, root_approved_at
               FROM org_invites WHERE org_id = ? AND status = ?
               ORDER BY created_at DESC""",
            (org_id, status_filter)
        )
    else:
        rows = db.fetch_all(
            """SELECT id, org_id, code, invite_type, target_username, target_user_id,
                      created_by, created_at, expires_at, max_uses, uses, status,
                      user_accepted, user_accepted_at, root_approved, root_approved_at
               FROM org_invites WHERE org_id = ?
               ORDER BY created_at DESC""",
            (org_id,)
        )
    
    return [_row_to_invite(row) for row in rows]


def get_user_pending_invites(user_id: int) -> List[OrgInvite]:
    """Get pending invites for a user."""
    db = _get_db()
    
    rows = db.fetch_all(
        """SELECT id, org_id, code, invite_type, target_username, target_user_id,
                  created_by, created_at, expires_at, max_uses, uses, status,
                  user_accepted, user_accepted_at, root_approved, root_approved_at
           FROM org_invites 
           WHERE target_user_id = ? AND status = 'pending' AND user_accepted = 0
           ORDER BY created_at DESC""",
        (user_id,)
    )
    
    return [_row_to_invite(row) for row in rows]


def accept_invite(invite_id: int, user_id: int) -> OrgInvite:
    """
    User accepts an invite (step 1 of 2-step flow).
    
    For existing user invites, this marks user_accepted=1.
    Root must still approve for the user to join.
    """
    db = _get_db()
    
    invite = get_invite(invite_id)
    if not invite:
        raise InviteNotFoundError(f"Invite {invite_id} not found")
    
    # Check if invite is for this user
    if invite.target_user_id and invite.target_user_id != user_id:
        raise PermissionDeniedError("This invite is not for you")
    
    # Check expiration
    if invite.expires_at and invite.expires_at < int(time.time()):
        raise InviteExpiredError("Invite has expired")
    
    # Check status
    if invite.status != "pending":
        raise OrganizationError(f"Invite is {invite.status}")
    
    now = int(time.time())
    db.execute(
        "UPDATE org_invites SET user_accepted = 1, user_accepted_at = ? WHERE id = ?",
        (now, invite_id)
    )
    
    org = get_org(invite.org_id)
    
    # If org doesn't require approval, auto-approve
    if not org.invite_requires_approval:
        return approve_invite(invite_id, org.root_user_id)
    
    logger.info(f"User {user_id} accepted invite {invite_id}")
    
    return get_invite(invite_id)


def approve_invite(invite_id: int, root_user_id: int) -> OrgInvite:
    """
    Root approves an invite (step 2 of 2-step flow).
    
    This completes the invite and adds the user to the org.
    """
    db = _get_db()
    
    invite = get_invite(invite_id)
    if not invite:
        raise InviteNotFoundError(f"Invite {invite_id} not found")
    
    # Check if root user
    if not is_org_root(invite.org_id, root_user_id):
        raise PermissionDeniedError("Only org root can approve invites")
    
    # Check if user has accepted
    if not invite.user_accepted:
        raise OrganizationError("User has not accepted the invite yet")
    
    # Check status
    if invite.status != "pending":
        raise OrganizationError(f"Invite is {invite.status}")
    
    now = int(time.time())
    
    # Add user to org
    add_member(invite.org_id, invite.target_user_id, "member", root_user_id)
    
    # Update invite
    db.execute(
        """UPDATE org_invites 
           SET root_approved = 1, root_approved_at = ?, status = 'completed', uses = uses + 1
           WHERE id = ?""",
        (now, invite_id)
    )
    
    logger.info(f"Root {root_user_id} approved invite {invite_id}")
    
    return get_invite(invite_id)


def reject_invite(invite_id: int, user_id: int) -> bool:
    """User rejects an invite."""
    db = _get_db()
    
    invite = get_invite(invite_id)
    if not invite:
        raise InviteNotFoundError(f"Invite {invite_id} not found")
    
    if invite.target_user_id and invite.target_user_id != user_id:
        raise PermissionDeniedError("This invite is not for you")
    
    db.execute("UPDATE org_invites SET status = 'rejected' WHERE id = ?", (invite_id,))
    
    logger.info(f"User {user_id} rejected invite {invite_id}")
    
    return True


def delete_invite(invite_id: int, root_user_id: int) -> bool:
    """Root deletes/cancels an invite."""
    db = _get_db()
    
    invite = get_invite(invite_id)
    if not invite:
        raise InviteNotFoundError(f"Invite {invite_id} not found")
    
    if not is_org_root(invite.org_id, root_user_id):
        raise PermissionDeniedError("Only org root can delete invites")
    
    db.execute("DELETE FROM org_invites WHERE id = ?", (invite_id,))
    
    logger.info(f"Root {root_user_id} deleted invite {invite_id}")
    
    return True


# === Root User Actions ===

def reset_user_password(root_user_id: int, target_user_id: int, new_password: str) -> bool:
    """Root resets a member's password."""
    db = _get_db()
    
    # Get user's org
    user_org = get_user_org(target_user_id)
    if not user_org:
        raise MemberNotFoundError("User not in any organization")
    
    if not is_org_root(user_org.id, root_user_id):
        raise PermissionDeniedError("Only org root can reset passwords")
    
    if not _get_config("root_user.can_reset_passwords", True):
        raise PermissionDeniedError("Password reset is disabled for this organization")
    
    # Cannot reset own password this way
    if root_user_id == target_user_id:
        raise PermissionDeniedError("Cannot reset your own password this way")
    
    # Use auth module to reset password
    if _auth:
        from src.utils.encryption import hash_password
        password_hash = hash_password(new_password)
        db.execute(
            "UPDATE auth_users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (password_hash, int(time.time()), target_user_id)
        )
        
        # Force logout all sessions
        force_logout(root_user_id, target_user_id)
        
        logger.info(f"Root {root_user_id} reset password for user {target_user_id}")
        return True
    
    return False


def lock_user(root_user_id: int, target_user_id: int) -> bool:
    """Root locks a member's account."""
    db = _get_db()
    
    user_org = get_user_org(target_user_id)
    if not user_org:
        raise MemberNotFoundError("User not in any organization")
    
    if not is_org_root(user_org.id, root_user_id):
        raise PermissionDeniedError("Only org root can lock accounts")
    
    if not _get_config("root_user.can_lock_accounts", True):
        raise PermissionDeniedError("Account locking is disabled")
    
    if root_user_id == target_user_id:
        raise PermissionDeniedError("Cannot lock your own account")
    
    db.execute(
        "UPDATE auth_users SET account_locked = 1, updated_at = ? WHERE id = ?",
        (int(time.time()), target_user_id)
    )
    
    force_logout(root_user_id, target_user_id)
    
    logger.info(f"Root {root_user_id} locked user {target_user_id}")
    
    return True


def unlock_user(root_user_id: int, target_user_id: int) -> bool:
    """Root unlocks a member's account."""
    db = _get_db()
    
    user_org = get_user_org(target_user_id)
    if not user_org:
        raise MemberNotFoundError("User not in any organization")
    
    if not is_org_root(user_org.id, root_user_id):
        raise PermissionDeniedError("Only org root can unlock accounts")
    
    db.execute(
        "UPDATE auth_users SET account_locked = 0, locked_until = NULL, updated_at = ? WHERE id = ?",
        (int(time.time()), target_user_id)
    )
    
    logger.info(f"Root {root_user_id} unlocked user {target_user_id}")
    
    return True


def force_logout(root_user_id: int, target_user_id: int) -> int:
    """Root forces logout of all user sessions."""
    db = _get_db()
    
    user_org = get_user_org(target_user_id)
    if not user_org:
        raise MemberNotFoundError("User not in any organization")
    
    if not is_org_root(user_org.id, root_user_id):
        raise PermissionDeniedError("Only org root can force logout")
    
    if not _get_config("root_user.can_force_logout", True):
        raise PermissionDeniedError("Force logout is disabled")
    
    # Revoke all sessions
    result = db.execute(
        "UPDATE auth_sessions SET revoked = 1 WHERE user_id = ? AND revoked = 0",
        (target_user_id,)
    )
    
    count = result.rowcount if hasattr(result, 'rowcount') else 0
    
    logger.info(f"Root {root_user_id} forced logout for user {target_user_id} ({count} sessions)")
    
    return count


def disinherit_user(root_user_id: int, target_user_id: int) -> bool:
    """Root removes user from org and moves to default org."""
    user_org = get_user_org(target_user_id)
    if not user_org:
        raise MemberNotFoundError("User not in any organization")
    
    if not is_org_root(user_org.id, root_user_id):
        raise PermissionDeniedError("Only org root can disinherit users")
    
    if root_user_id == target_user_id:
        raise PermissionDeniedError("Cannot disinherit yourself")
    
    return remove_member(user_org.id, target_user_id, disinherit=True)


# === Managed Settings ===

def get_managed_settings(org_id: int) -> List[OrgManagedSetting]:
    """Get all managed settings for an organization."""
    db = _get_db()
    
    rows = db.fetch_all(
        """SELECT id, org_id, setting_key, setting_value, locked
           FROM org_managed_settings WHERE org_id = ?""",
        (org_id,)
    )
    
    return [
        OrgManagedSetting(
            id=row["id"],
            org_id=row["org_id"],
            setting_key=row["setting_key"],
            setting_value=row["setting_value"],
            locked=bool(row["locked"])
        )
        for row in rows
    ]


def set_managed_setting(org_id: int, root_user_id: int, key: str, value: str, locked: bool = True) -> OrgManagedSetting:
    """Set a managed setting for an organization."""
    db = _get_db()
    
    if not is_org_root(org_id, root_user_id):
        raise PermissionDeniedError("Only org root can manage settings")
    
    # Check if setting is manageable
    manageable = _get_config("manageable_settings", [])
    if key not in manageable:
        raise OrganizationError(f"Setting '{key}' cannot be managed by organization")
    
    from src.utils.encryption import generate_snowflake_id
    
    existing = db.fetch_one(
        "SELECT id FROM org_managed_settings WHERE org_id = ? AND setting_key = ?",
        (org_id, key)
    )
    
    if existing:
        db.execute(
            "UPDATE org_managed_settings SET setting_value = ?, locked = ? WHERE id = ?",
            (value, 1 if locked else 0, existing["id"])
        )
    else:
        setting_id = generate_snowflake_id()
        db.execute(
            """INSERT INTO org_managed_settings (id, org_id, setting_key, setting_value, locked)
               VALUES (?, ?, ?, ?, ?)""",
            (setting_id, org_id, key, value, 1 if locked else 0)
        )
    
    logger.info(f"Root {root_user_id} set managed setting '{key}' for org {org_id}")
    
    settings = get_managed_settings(org_id)
    return next((s for s in settings if s.setting_key == key), None)


def is_setting_locked(user_id: int, setting_key: str) -> bool:
    """Check if a setting is locked for a user by their org."""
    org = get_user_org(user_id)
    if not org:
        return False
    
    db = _get_db()
    row = db.fetch_one(
        "SELECT locked FROM org_managed_settings WHERE org_id = ? AND setting_key = ? AND locked = 1",
        (org.id, setting_key)
    )
    
    return row is not None


def get_locked_setting_value(user_id: int, setting_key: str) -> Optional[str]:
    """Get the locked value for a setting if it's org-managed."""
    org = get_user_org(user_id)
    if not org:
        return None
    
    db = _get_db()
    row = db.fetch_one(
        "SELECT setting_value FROM org_managed_settings WHERE org_id = ? AND setting_key = ? AND locked = 1",
        (org.id, setting_key)
    )
    
    return row["setting_value"] if row else None


# === Server Restrictions ===

def can_join_server(user_id: int, server_id: int) -> bool:
    """Check if user can join a server based on org restrictions."""
    org = get_user_org(user_id)
    if not org:
        return True
    
    # Check blocked servers
    if org.blocked_servers and server_id in org.blocked_servers:
        return False
    
    # Check allowed servers (if set, only those are allowed)
    if org.allowed_servers is not None and server_id not in org.allowed_servers:
        return False
    
    return True


def update_server_restrictions(
    org_id: int,
    root_user_id: int,
    default_servers: List[int] = None,
    allowed_servers: List[int] = None,
    blocked_servers: List[int] = None
) -> Organization:
    """Update server restrictions for an organization."""
    db = _get_db()
    
    if not is_org_root(org_id, root_user_id):
        raise PermissionDeniedError("Only org root can update server restrictions")
    
    updates = []
    params = []
    
    if default_servers is not None:
        updates.append("default_servers = ?")
        params.append(json.dumps(default_servers))
    
    if allowed_servers is not None:
        updates.append("allowed_servers = ?")
        params.append(json.dumps(allowed_servers) if allowed_servers else None)
    
    if blocked_servers is not None:
        updates.append("blocked_servers = ?")
        params.append(json.dumps(blocked_servers))
    
    if updates:
        updates.append("updated_at = ?")
        params.append(int(time.time()))
        params.append(org_id)
        
        db.execute(
            f"UPDATE organizations SET {', '.join(updates)} WHERE id = ?",
            tuple(params)
        )
    
    logger.info(f"Root {root_user_id} updated server restrictions for org {org_id}")
    
    return get_org(org_id)


__all__ = [
    'setup', 'is_setup',
    'create_org', 'get_org', 'get_org_by_name', 'get_default_org',
    'get_user_org', 'get_org_members', 'get_member', 'add_member', 'remove_member',
    'is_org_root',
    'create_invite', 'get_invite', 'get_invite_by_code', 'get_org_invites',
    'get_user_pending_invites', 'accept_invite', 'approve_invite', 'reject_invite', 'delete_invite',
    'reset_user_password', 'lock_user', 'unlock_user', 'force_logout', 'disinherit_user',
    'get_managed_settings', 'set_managed_setting', 'is_setting_locked', 'get_locked_setting_value',
    'can_join_server', 'update_server_restrictions',
    'Organization', 'OrgMember', 'OrgInvite', 'OrgManagedSetting', 'OrgRole',
    'OrganizationError', 'OrgNotFoundError', 'OrgExistsError',
    'MemberNotFoundError', 'InviteNotFoundError', 'InviteExpiredError',
    'PermissionDeniedError', 'FeatureRequiredError',
]
