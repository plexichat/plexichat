"""
Protocol for embed manager mixins providing cross-mixin method signatures.

Used by embed mixins to declare which methods they expect from sibling
mixins when combined via multiple inheritance.
"""

from typing import Any, Dict, Optional

from src.core.base import SnowflakeID


class EmbedManagerProtocol:
    _db: Any
    _config: Dict[str, Any]
    _validate_embed_data: Any

    def get_embed(self, embed_id: SnowflakeID) -> Optional[Any]: ...

    def _get_message(self, message_id: SnowflakeID) -> Optional[Dict[str, Any]]: ...

    def _is_participant(
        self, conversation_id: SnowflakeID, user_id: SnowflakeID
    ) -> bool: ...

    def _get_channel_for_conversation(
        self, conversation_id: SnowflakeID
    ) -> Optional[Dict[str, Any]]: ...

    def _check_embed_links_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID] = None,
    ) -> bool: ...

    def _get_timestamp(self) -> int: ...

    def _generate_id(self) -> SnowflakeID: ...

    def _row_to_embed(self, row: Dict[str, Any]) -> Any: ...

    def attach_embed_to_message(
        self, user_id: SnowflakeID, message_id: SnowflakeID, embed_id: SnowflakeID
    ) -> bool: ...
