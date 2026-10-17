"""
Protocol for embed manager mixins providing cross-mixin method signatures.

Used by embed mixins to declare which methods they expect from sibling
mixins when combined via multiple inheritance.
"""

from typing import Any, Callable, Dict, Optional

from src.core.base import SnowflakeID


class EmbedManagerProtocol:
    _db: Any
    _config: Dict[str, Any]
    _validate_embed_data: Any

    _get_timestamp: Callable[[], int]
    _generate_id: Callable[[], SnowflakeID]

    get_embed: Callable[[SnowflakeID], Optional[Any]]
    _get_message: Callable[[SnowflakeID], Optional[Dict[str, Any]]]
    _is_participant: Callable[[SnowflakeID, SnowflakeID], bool]
    _get_channel_for_conversation: Callable[[SnowflakeID], Optional[Dict[str, Any]]]
    _check_embed_links_permission: Callable[
        [SnowflakeID, SnowflakeID, Optional[SnowflakeID]], bool
    ]
    _row_to_embed: Callable[[Dict[str, Any]], Any]

    def attach_embed_to_message(
        self,
        user_id: SnowflakeID,
        message_id: SnowflakeID,
        embed_id: SnowflakeID,
        position: Optional[int] = None,
    ) -> bool: ...
