import json
from typing import Optional, List, Dict, Any

import utils.logger as logger
from src.core.base import SnowflakeID
from ..models import (
    Server,
    Channel,
    ChannelCategory,
    Role,
    Member,
    ChannelOverride,
    Invite,
    Ban,
    AuditLogEntry,
    ChannelType,
    AuditLogAction,
)


def _row_to_server(row: Dict[str, Any], encrypt_descriptions: bool = False) -> Server:
    """Convert database row to Server model."""
    member_count = 0
    channel_count = 0
    role_count = 0
    try:
        member_count = row["member_count"]
    except (KeyError, IndexError):
        pass
    try:
        channel_count = row["channel_count"]
    except (KeyError, IndexError):
        pass
    try:
        role_count = row["role_count"]
    except (KeyError, IndexError):
        pass

    default_channel_id = None
    try:
        default_channel_id = row["default_channel_id"]
    except (KeyError, IndexError):
        pass

    description = None
    if row.get("description_encrypted"):
        if encrypt_descriptions:
            from src.utils.encryption import decrypt_data

            try:
                description = decrypt_data(row["description_encrypted"])
            except Exception as e:
                logger.warning(f"Failed to decrypt server description {row['id']}: {e}")
        else:
            description = row["description_encrypted"]

    return Server(
        id=row["id"],
        name=row["name"],
        owner_id=row["owner_id"],
        description=description,
        icon_path=row["icon_url"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        member_count=member_count,
        channel_count=channel_count,
        role_count=role_count,
        default_role_id=row["default_role_id"],
        default_channel_id=default_channel_id,
        system_channel_id=row["system_channel_id"],
        verification_level=row["verification_level"],
        deleted=bool(row["deleted"]),
        deleted_at=row["deleted_at"],
        metadata=json.loads(row["metadata"]) if row["metadata"] else None,
    )


def _server_to_dict(server: Server) -> Dict[str, Any]:
    """Convert Server model to dict for caching."""
    return {
        "id": server.id,
        "name": server.name,
        "owner_id": server.owner_id,
        "description": server.description,
        "icon_path": server.icon_path,
        "banner_path": server.banner_path,
        "created_at": server.created_at,
        "updated_at": server.updated_at,
        "member_count": server.member_count,
        "channel_count": server.channel_count,
        "role_count": server.role_count,
        "default_role_id": server.default_role_id,
        "default_channel_id": server.default_channel_id,
        "system_channel_id": server.system_channel_id,
        "verification_level": server.verification_level,
        "deleted": server.deleted,
        "deleted_at": server.deleted_at,
        "metadata": server.metadata,
    }


def _dict_to_server(data: Dict[str, Any]) -> Server:
    """Convert cached dict to Server model."""
    return Server(
        id=data["id"],
        name=data["name"],
        owner_id=data["owner_id"],
        description=data.get("description"),
        icon_path=data.get("icon_path") or data.get("icon_url"),
        banner_path=data.get("banner_path") or data.get("banner_url"),
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        member_count=data.get("member_count", 0),
        channel_count=data.get("channel_count", 0),
        role_count=data.get("role_count", 0),
        default_role_id=data.get("default_role_id"),
        default_channel_id=data.get("default_channel_id"),
        system_channel_id=data.get("system_channel_id"),
        verification_level=data.get("verification_level", 0),
        deleted=data.get("deleted", False),
        deleted_at=data.get("deleted_at"),
        metadata=data.get("metadata"),
    )


def _row_to_channel(row: Dict[str, Any], encrypt_descriptions: bool = False) -> Channel:
    """Convert database row to Channel model."""
    topic = None
    if row.get("topic_encrypted"):
        if encrypt_descriptions:
            from src.utils.encryption import decrypt_data

            try:
                topic = decrypt_data(row["topic_encrypted"])
            except Exception as e:
                logger.warning(f"Failed to decrypt channel topic {row['id']}: {e}")
        else:
            topic = row["topic_encrypted"]

    return Channel(
        id=row["id"],
        server_id=row["server_id"],
        name=row["name"],
        channel_type=ChannelType(row["channel_type"]),
        category_id=row["category_id"],
        position=row["position"],
        topic=topic,
        nsfw=bool(row["nsfw"]),
        slowmode_seconds=row.get("slowmode_seconds", 0),
        read_receipts_enabled=bool(row.get("read_receipts_enabled", True)),
        conversation_id=row.get("conversation_id"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted=bool(row.get("deleted", False)),
        deleted_at=row.get("deleted_at"),
        metadata=json.loads(row["metadata"]) if row.get("metadata") else None,
    )


def _row_to_category(row: Dict[str, Any]) -> ChannelCategory:
    """Convert database row to ChannelCategory model."""
    return ChannelCategory(
        id=row["id"],
        server_id=row["server_id"],
        name=row["name"],
        position=row["position"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_role(row: Dict[str, Any]) -> Role:
    """Convert database row to Role model."""
    perms = row["permissions"]
    if isinstance(perms, str):
        perms = json.loads(perms) if perms else {}

    return Role(
        id=row["id"],
        server_id=row["server_id"],
        name=row["name"],
        permissions=perms or {},
        color=row["color"],
        hoist=bool(row["hoist"]),
        mentionable=bool(row["mentionable"]),
        position=row["position"],
        is_default=bool(row.get("is_default", False)),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted=bool(row.get("deleted", False)),
    )


def _row_to_member(
    row: Dict[str, Any],
    roles: Optional[List[SnowflakeID]] = None,
    username: Optional[str] = None,
    avatar_url: Optional[str] = None,
) -> Member:
    """Convert database row to Member model."""
    return Member(
        id=row["id"],
        server_id=row["server_id"],
        user_id=row["user_id"],
        nickname=row.get("nickname"),
        username=username,
        avatar_url=avatar_url,
        joined_at=row["joined_at"],
        updated_at=row.get("updated_at", row["joined_at"]),
        muted=bool(row.get("muted", False)),
        deafened=bool(row.get("deafened", False)),
        inviter_id=row.get("inviter_id"),
        timeout_until=row.get("timeout_until"),
        timeout_reason=row.get("timeout_reason"),
        roles=roles or [],
    )


def _row_to_override(row: Dict[str, Any]) -> ChannelOverride:
    """Convert database row to ChannelOverride model."""
    allow = row["allow"]
    deny = row["deny"]

    if isinstance(allow, str):
        allow = json.loads(allow) if allow else {}
    if isinstance(deny, str):
        deny = json.loads(deny) if deny else {}

    return ChannelOverride(
        id=row["id"],
        channel_id=row["channel_id"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        allow=allow or {},
        deny=deny or {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_invite(row: Dict[str, Any]) -> Invite:
    """Convert database row to Invite model."""
    return Invite(
        id=row["id"],
        code=row["code"],
        server_id=row["server_id"],
        channel_id=row["channel_id"],
        inviter_id=row["inviter_id"],
        max_age=row["max_age"],
        max_uses=row["max_uses"],
        uses=row["uses"],
        temporary=bool(row["temporary"]),
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        revoked=bool(row["revoked"]),
    )


def _row_to_ban(row: Dict[str, Any]) -> Ban:
    """Convert database row to Ban model."""
    return Ban(
        id=row["id"],
        server_id=row["server_id"],
        user_id=row["user_id"],
        banned_by=row["banned_by"],
        reason=row["reason"],
        created_at=row["created_at"],
    )


def _row_to_audit_entry(row: Dict[str, Any]) -> AuditLogEntry:
    """Convert database row to AuditLogEntry model."""
    changes = row.get("changes")
    if isinstance(changes, str):
        try:
            changes = json.loads(changes) if changes else None
        except json.JSONDecodeError:
            changes = None

    action_val = row.get("action_type") or row.get("action")
    action_type = AuditLogAction.SERVER_UPDATE
    if action_val:
        if isinstance(action_val, str):
            action_val = action_val.lower().replace("-", "_")

        try:
            action_type = AuditLogAction(action_val)
        except (ValueError, KeyError):
            try:
                action_type = AuditLogAction[action_val.upper()]
            except (ValueError, KeyError):
                action_type = AuditLogAction.SERVER_UPDATE

    return AuditLogEntry(
        id=row["id"],
        server_id=row["server_id"],
        user_id=row["user_id"],
        action_type=action_type,
        target_type=row.get("target_type"),
        target_id=row.get("target_id"),
        changes=changes,
        reason=row.get("reason"),
        created_at=row["created_at"],
    )
