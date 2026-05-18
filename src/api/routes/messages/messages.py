"""
Message utilities - Helper functions for message routes.
"""

from typing import Optional, Any


def get_msg_id(message: Any) -> Optional[int]:
    """
    Helper to extract message ID robustly from different object types.

    Args:
        message: Message object (could be object or dict)

    Returns:
        Message ID as int if found, None otherwise
    """
    if message is None:
        return None
    return getattr(message, "id", None) or (
        message.get("id") if isinstance(message, dict) else None
    )
