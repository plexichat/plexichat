"""
Permission system for PlexiChat authentication.

Permissions are hierarchical with wildcard support:
- "messages.send" - specific permission
- "messages.*" - all message permissions
- "*" - all permissions (admin)

Server/DM permissions will be handled separately in the messaging module,
using these base permissions as a foundation.
"""

from typing import Dict, List, Optional, Tuple
import json

# All available permissions with descriptions
PERMISSIONS = {
    # Messaging
    "messages.send": "Send messages in conversations",
    "messages.read": "Read messages in conversations",
    "messages.edit": "Edit own messages",
    "messages.delete": "Delete own messages",
    "messages.delete_others": "Delete others messages (moderator)",
    "messages.pin": "Pin messages",
    "messages.react": "Add reactions to messages",
    # Conversations (DMs and Groups)
    "conversations.create": "Create new conversations",
    "conversations.join": "Join conversations",
    "conversations.leave": "Leave conversations",
    "conversations.invite": "Invite others to conversations",
    "conversations.kick": "Remove others from conversations",
    "conversations.manage": "Manage conversation settings",
    "conversations.delete": "Delete conversations",
    # Servers (community servers)
    "servers.create": "Create new servers",
    "servers.join": "Join servers",
    "servers.leave": "Leave servers",
    "servers.manage": "Manage server settings",
    "servers.delete": "Delete servers",
    "servers.invite": "Create server invites",
    # Channels within servers (future)
    "channels.create": "Create channels in servers",
    "channels.manage": "Manage channel settings",
    "channels.delete": "Delete channels",
    # Roles within servers (future)
    "roles.create": "Create roles in servers",
    "roles.manage": "Manage role permissions",
    "roles.assign": "Assign roles to members",
    "roles.delete": "Delete roles",
    # Voice/Video (future)
    "voice.join": "Join voice channels",
    "voice.speak": "Speak in voice channels",
    "voice.mute_others": "Mute other users",
    "voice.deafen_others": "Deafen other users",
    "voice.move_others": "Move users between channels",
    "voice.initiate": "Start voice calls",
    "video.join": "Join video calls",
    "video.stream": "Stream video",
    "video.initiate": "Start video calls",
    # Account
    "account.edit_profile": "Edit own profile",
    "account.delete": "Delete own account",
    "account.view_others": "View other user profiles",
    # Bots
    "bots.create": "Create bot accounts",
    "bots.manage": "Manage own bots",
    # Moderation (future)
    "moderation.ban": "Ban users from servers",
    "moderation.kick": "Kick users from servers",
    "moderation.mute": "Mute users in servers",
    "moderation.warn": "Warn users",
    "moderation.view_audit": "View moderation audit log",
    # Admin
    "admin.users": "Manage all users",
    "admin.servers": "Manage all servers",
    "admin.system": "System administration",
}

# Default permissions for new user accounts
DEFAULT_USER_PERMISSIONS = {
    "messages.send": True,
    "messages.read": True,
    "messages.edit": True,
    "messages.delete": True,
    "messages.react": True,
    "conversations.create": True,
    "conversations.join": True,
    "conversations.leave": True,
    "conversations.invite": True,
    "servers.create": True,
    "servers.join": True,
    "servers.leave": True,
    "voice.join": True,
    "voice.speak": True,
    "voice.initiate": True,
    "video.join": True,
    "video.stream": True,
    "video.initiate": True,
    "account.edit_profile": True,
    "account.delete": True,
    "account.view_others": True,
    "bots.create": True,
    "bots.manage": True,
}

# Default permissions for bot accounts (more restricted)
DEFAULT_BOT_PERMISSIONS = {
    "messages.send": True,
    "messages.read": True,
    "messages.edit": True,
    "messages.delete": True,
    "messages.react": True,
    "conversations.join": True,
    "conversations.leave": True,
    "account.view_others": True,
    # Bots cannot create other bots
    # Bots cannot initiate voice/video by default
    # Bots cannot delete accounts
}

# Permissions that bots can never have
BOT_RESTRICTED_PERMISSIONS = {
    "bots.create",
    "bots.manage",
    "account.delete",
    "admin.users",
    "admin.servers",
    "admin.system",
}


def has_permission(user_permissions: Optional[Dict[str, bool]], required: str) -> bool:
    """
    Check if user has a specific permission.

    Supports wildcards:
    - "messages.*" grants all permissions starting with "messages."
    - "*" grants all permissions

    Args:
        user_permissions: Dict of permission -> bool
        required: The permission to check

    Returns:
        True if user has the permission
    """
    if not user_permissions:
        return False

    # Check full wildcard first
    if user_permissions.get("*", False):
        return True

    # Check exact match
    if user_permissions.get(required, False):
        return True

    # Check wildcard matches
    # e.g., if required is "messages.send", check for "messages.*"
    parts = required.split(".")
    for i in range(len(parts)):
        wildcard = ".".join(parts[: i + 1]) + ".*"
        if user_permissions.get(wildcard, False):
            return True

    return False


def validate_permissions(
    permissions: Dict[str, bool], is_bot: bool = False
) -> Tuple[bool, List[str]]:
    """
    Validate a permissions dictionary.

    Args:
        permissions: Dict of permission -> bool
        is_bot: Whether this is for a bot account

    Returns:
        Tuple of (valid: bool, issues: list[str])
    """
    issues = []

    for perm, value in permissions.items():
        # Check if permission exists (allow wildcards)
        if perm != "*" and not perm.endswith(".*"):
            if perm not in PERMISSIONS:
                issues.append(f"Unknown permission: {perm}")

        # Check bot restrictions
        if is_bot and value:
            if perm in BOT_RESTRICTED_PERMISSIONS:
                issues.append(f"Bots cannot have permission: {perm}")

            # Check wildcard grants of restricted permissions
            if perm == "*" or perm.endswith(".*"):
                for restricted in BOT_RESTRICTED_PERMISSIONS:
                    if restricted.startswith(perm.replace(".*", "")):
                        issues.append(
                            f"Bots cannot have permission: {restricted} (via {perm})"
                        )

    return len(issues) == 0, issues


def merge_permissions(
    base: Dict[str, bool], override: Dict[str, bool]
) -> Dict[str, bool]:
    """
    Merge two permission dictionaries.
    Override takes precedence.

    Args:
        base: Base permissions
        override: Permissions to override with

    Returns:
        Merged permissions dict
    """
    result = base.copy()
    result.update(override)
    return result


def permissions_to_json(permissions: Dict[str, bool]) -> str:
    """Serialize permissions to JSON string for database storage."""
    return json.dumps(permissions)


def permissions_from_json(json_str: str) -> Dict[str, bool]:
    """Deserialize permissions from JSON string."""
    if not json_str:
        return {}
    return json.loads(json_str)


def get_permission_categories() -> Dict[str, List[str]]:
    """
    Get permissions organized by category.

    Returns:
        Dict of category -> list of permissions
    """
    categories = {}
    for perm in PERMISSIONS:
        category = perm.split(".")[0]
        if category not in categories:
            categories[category] = []
        categories[category].append(perm)
    return categories
