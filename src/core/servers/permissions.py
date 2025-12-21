"""
Server permissions - Permission calculation with role hierarchy and channel overrides.
"""

from typing import Dict, List, Optional, Any

from .models import SERVER_PERMISSIONS


def calculate_base_permissions(roles: List[Dict[str, Any]], is_owner: bool = False) -> Dict[str, bool]:
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
        return {perm: True for perm in SERVER_PERMISSIONS}

    result = {}

    for role in roles:
        role_perms = role.get("permissions", {})
        if isinstance(role_perms, str):
            import json
            try:
                role_perms = json.loads(role_perms)
            except (json.JSONDecodeError, TypeError):
                role_perms = {}

        if not role_perms:
            continue

        # Administrator bypasses all permissions
        if role_perms.get("administrator"):
            return {perm: True for perm in SERVER_PERMISSIONS}

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
    2. Apply role-specific overrides (deny first, then allow)
    3. Apply member-specific override (deny first, then allow)
    
    Args:
        base_permissions: Base permissions from roles
        role_overrides: List of role override dicts with 'allow' and 'deny' keys
        member_override: Optional member-specific override
        
    Returns:
        Final permissions dict
    """
    result = dict(base_permissions)

    # If user has administrator, skip overrides
    if result.get("administrator"):
        return {perm: True for perm in SERVER_PERMISSIONS}

    # Apply role overrides
    role_allow = {}
    role_deny = {}

    for override in role_overrides:
        allow = override.get("allow", {})
        deny = override.get("deny", {})

        if isinstance(allow, str):
            import json
            try:
                allow = json.loads(allow)
            except (json.JSONDecodeError, TypeError):
                allow = {}

        if isinstance(deny, str):
            import json
            try:
                deny = json.loads(deny)
            except (json.JSONDecodeError, TypeError):
                deny = {}

        for perm, value in allow.items():
            if value:
                role_allow[perm] = True

        for perm, value in deny.items():
            if value:
                role_deny[perm] = True

    # Apply role denies first
    for perm in role_deny:
        result[perm] = False

    # Then role allows
    for perm in role_allow:
        result[perm] = True

    # Apply member override last
    if member_override:
        allow = member_override.get("allow", {})
        deny = member_override.get("deny", {})

        if isinstance(allow, str):
            import json
            try:
                allow = json.loads(allow)
            except (json.JSONDecodeError, TypeError):
                allow = {}

        if isinstance(deny, str):
            import json
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


def can_manage_role(user_roles: List[Dict[str, Any]], target_role: Dict[str, Any], is_owner: bool = False) -> bool:
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
