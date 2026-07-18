"""Message operations - send messages, get messages, audit log."""

from typing import Any, Dict, List, Optional

from src.core.base import SnowflakeID

from .models import AuditLogEntry, AuditLogAction

_manager: Any = None


def _get_manager() -> Any:
    """Get the server manager instance."""
    global _manager
    if _manager is None:
        from . import _get_manager as _get_global_manager

        _manager = _get_global_manager()
    return _manager


def send_channel_message(
    user_id: SnowflakeID,
    channel_id: SnowflakeID,
    content: str,
    attachments: Optional[List[Dict[str, Any]]] = None,
    reply_to_id: Optional[SnowflakeID] = None,
) -> Any:
    """Send a message to a text channel."""
    return _get_manager().send_channel_message(
        user_id, channel_id, content, attachments, reply_to_id
    )


def get_channel_messages(
    user_id: SnowflakeID,
    channel_id: SnowflakeID,
    limit: int = 50,
    before_id: Optional[SnowflakeID] = None,
    after_id: Optional[SnowflakeID] = None,
) -> List[Any]:
    """Get messages from a text channel."""
    return _get_manager().get_channel_messages(
        user_id, channel_id, limit, before_id, after_id
    )


def get_audit_log(
    user_id: SnowflakeID,
    server_id: SnowflakeID,
    limit: int = 50,
    action_type: Optional[AuditLogAction] = None,
    before_id: Optional[SnowflakeID] = None,
) -> List[AuditLogEntry]:
    """Get audit log entries for a server."""
    return _get_manager().get_audit_log(
        user_id, server_id, limit, action_type, before_id
    )
