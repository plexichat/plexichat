"""
Permission utility functions - Pure functions for permission calculation.

These are stateless utility functions used by handlers for permission logic.
"""

import json
from typing import Any, Dict, List, Optional

from src.core.security.hardening import HardeningViolation
from src.core.security.hardening.text import reject_control_characters

from .models import SERVER_PERMISSIONS
from .exceptions import InvalidPermissionKeyError, PermissionDeniedError

#: A role permission dict never legitimately exceeds the known vocabulary.
MAX_PERMISSION_ENTRIES = len(SERVER_PERMISSIONS)
#: Individual keys are short dotted identifiers; cap hostile string input.
MAX_PERMISSION_KEY_LENGTH = 64

#: The full-grant permission set is a request-invariant vocabulary: it depends
#: only on the static ``SERVER_PERMISSIONS`` catalog. Bulk member-list reads
#: resolve permissions once per member of a page, and an owner or an
#: administrator used to rebuild this dict comprehension on every resolution
#: (and again when channel overrides were applied) -- the same N keys inserted
#: over and over for one request. Build the template once; every caller gets a
#: fresh shallow copy so timeout restrictions and overrides still mutate only
#: their own result, never the shared template or another member's verdict.
_ALL_PERMISSIONS = {perm: True for perm in SERVER_PERMISSIONS}


def validate_permission_dict(permissions: Optional[Dict[str, Any]]) -> None:
    """
    Validate a role permission dict against the SERVER_PERMISSIONS vocabulary.

    Rejects wildcard keys ("*" and "namespace.*") and any key that is not a
    defined server permission. Wildcards must never reach stored role
    permissions because has_permission() expands them into full grants.

    Raises:
        InvalidPermissionKeyError: On wildcard or unknown permission keys.
    """
    if not permissions:
        return
    if len(permissions) > MAX_PERMISSION_ENTRIES:
        raise InvalidPermissionKeyError(
            f"A role may carry at most {MAX_PERMISSION_ENTRIES} permission entries"
        )
    for key, value in permissions.items():
        if not isinstance(key, str):
            raise InvalidPermissionKeyError("Permission keys must be strings")
        try:
            key = reject_control_characters(key, "Permission key")
        except HardeningViolation as exc:
            raise InvalidPermissionKeyError(str(exc), key) from exc
        if len(key) > MAX_PERMISSION_KEY_LENGTH:
            raise InvalidPermissionKeyError(
                f"Permission key is too long: {key[:32]}...", key
            )
        # Permissions are booleans. A string/list value must never reach the
        # grant logic, where any truthy object would flip a permission on.
        if not isinstance(value, bool):
            raise InvalidPermissionKeyError(
                f"Permission value for {key} must be a boolean", key
            )
        if key == "*" or key.endswith(".*"):
            raise InvalidPermissionKeyError(
                f"Wildcard permissions are not allowed: {key}", key
            )
        if key not in SERVER_PERMISSIONS:
            raise InvalidPermissionKeyError(f"Unknown permission: {key}", key)


def check_permissions_grantable(
    editor_permissions: Dict[str, bool],
    requested: Optional[Dict[str, Any]],
) -> None:
    """
    Ensure the editor only grants permissions they themselves possess.

    Args:
        editor_permissions: The editor's resolved permission set in the server.
        requested: The permission dict being granted to the role.

    Raises:
        PermissionDeniedError: If a truthy requested permission is not held
            by the editor.
    """
    if not requested:
        return
    for perm, value in requested.items():
        if value and not editor_permissions.get(perm):
            raise PermissionDeniedError(
                f"Cannot grant a permission you do not possess: {perm}", perm
            )


def calculate_base_permissions(
    roles: List[Dict[str, Any]], is_owner: bool = False
) -> Dict[str, bool]:
    """
    Calculate base permissions from a list of roles.

    Permissions are combined using OR - if any role grants a permission, it is granted.

    Args:
        roles: List of role dicts with 'permissions' key
        is_owner: Whether the user is the server owner

    Returns:
        Dict of permission name to boolean
    """
    if is_owner:
        return dict(_ALL_PERMISSIONS)

    result = {}

    for role in roles:
        role_perms = role.get("permissions", {})
        if isinstance(role_perms, str):
            try:
                role_perms = json.loads(role_perms)
            except (json.JSONDecodeError, TypeError):
                role_perms = {}

        if not role_perms:
            continue

        # Administrator bypasses all permissions
        if role_perms.get("administrator"):
            return dict(_ALL_PERMISSIONS)

        for perm, value in role_perms.items():
            if value:
                result[perm] = True

    return result


def apply_channel_overrides(
    base_permissions: Dict[str, bool],
    role_overrides: List[Dict[str, Any]],
    member_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, bool]:
    """
    Apply channel permission overrides to base permissions.

    Order of application:
    1. Start with base permissions from roles
    2. Apply role-specific overrides one role at a time, LOWEST position
       first (deny then allow per role)
    3. Apply member-specific override last (deny then allow)

    Applying each role's overwrite in position order (lowest first) means
    the highest-position role's decision wins for every permission: a deny
    on a high role beats an allow on a lower role, mirroring Discord's
    hierarchy semantics. Flattening every deny before every allow would let
    the lowest role's allow override the highest role's deny.

    Args:
        base_permissions: Base permissions from roles
        role_overrides: List of role override dicts with 'allow' and 'deny'
            keys, ordered LOWEST role position first (callers sort by
            srv_roles.position ascending)
        member_override: Optional member-specific override

    Returns:
        Final permissions dict
    """
    result = dict(base_permissions)

    # If user has administrator, skip overrides
    if result.get("administrator"):
        return dict(_ALL_PERMISSIONS)

    # Apply role overrides in the order given (lowest position first). Each
    # override is applied deny-then-allow, so later (higher-position) roles
    # win conflicts and within a single override allow still beats deny,
    # preserving the stored allow/deny semantics of a lone overwrite.
    for override in role_overrides:
        allow = override.get("allow", {})
        deny = override.get("deny", {})

        if isinstance(allow, str):
            try:
                allow = json.loads(allow)
            except (json.JSONDecodeError, TypeError):
                allow = {}

        if isinstance(deny, str):
            try:
                deny = json.loads(deny)
            except (json.JSONDecodeError, TypeError):
                deny = {}

        for perm, value in deny.items():
            if value:
                result[perm] = False

        for perm, value in allow.items():
            if value:
                result[perm] = True

    # Apply member override last
    if member_override:
        allow = member_override.get("allow", {})
        deny = member_override.get("deny", {})

        if isinstance(allow, str):
            try:
                allow = json.loads(allow)
            except (json.JSONDecodeError, TypeError):
                allow = {}

        if isinstance(deny, str):
            try:
                deny = json.loads(deny)
            except (json.JSONDecodeError, TypeError):
                deny = {}

        # Deny first
        for perm, value in deny.items():
            if value:
                result[perm] = False

        # Then allow
        for perm, value in allow.items():
            if value:
                result[perm] = True

    return result


def has_permission(permissions: Dict[str, bool], permission: str) -> bool:
    """
    Check if a permission is granted.

    Supports wildcard permissions (e.g., 'messages.*' matches 'messages.send').

    Args:
        permissions: Dict of permission name to boolean
        permission: Permission to check

    Returns:
        True if permission is granted
    """
    # Administrator grants all permissions
    if permissions.get("administrator"):
        return True

    # Direct match
    if permissions.get(permission):
        return True

    # Check for wildcard match
    parts = permission.split(".")
    if len(parts) > 1:
        wildcard = f"{parts[0]}.*"
        if permissions.get(wildcard):
            return True

    # Check if user has full wildcard
    if permissions.get("*"):
        return True

    return False


def get_highest_role_position(roles: List[Dict[str, Any]]) -> int:
    """
    Get the highest role position from a list of roles.

    Higher position = more authority.

    Args:
        roles: List of role dicts with 'position' key

    Returns:
        Highest position value, or 0 if no roles
    """
    if not roles:
        return 0

    return max(role.get("position", 0) for role in roles)


def can_manage_role(
    user_roles: List[Dict[str, Any]],
    target_role: Dict[str, Any],
    is_owner: bool = False,
) -> bool:
    """
    Check if user can manage a target role based on hierarchy.

    User can only manage roles below their highest role.

    Args:
        user_roles: List of user's role dicts
        target_role: Target role dict
        is_owner: Whether user is server owner

    Returns:
        True if user can manage the role
    """
    if is_owner:
        return True

    user_position = get_highest_role_position(user_roles)
    target_position = target_role.get("position", 0)

    return user_position > target_position


def can_manage_member(
    user_roles: List[Dict[str, Any]],
    target_roles: List[Dict[str, Any]],
    is_owner: bool = False,
    target_is_owner: bool = False,
) -> bool:
    """
    Check if user can manage a target member based on role hierarchy.

    Args:
        user_roles: List of user's role dicts
        target_roles: List of target member's role dicts
        is_owner: Whether user is server owner
        target_is_owner: Whether target is server owner

    Returns:
        True if user can manage the member
    """
    # Cannot manage the owner
    if target_is_owner:
        return False

    # Owner can manage anyone
    if is_owner:
        return True

    user_position = get_highest_role_position(user_roles)
    target_position = get_highest_role_position(target_roles)

    return user_position > target_position
