from typing import Any, Dict, Optional

from src.core.base import SnowflakeID
from ..models import Reaction, CustomEmoji


class ReactionProtocol:
    _db: Any = None
    _auth: Any = None
    _messaging: Any = None
    _servers: Any = None
    _relationships: Any = None
    _media: Any = None
    _config: Dict[str, Any] = {}

    def _get_timestamp(self) -> int:
        return super()._get_timestamp()  # type: ignore[misc]

    def _generate_id(self) -> int:
        return super()._generate_id()  # type: ignore[misc]

    def _user_exists(self, user_id: SnowflakeID) -> bool:
        return super()._user_exists(user_id)  # type: ignore[misc]

    def _get_message(self, message_id: SnowflakeID) -> Optional[Dict]:
        return super()._get_message(message_id)  # type: ignore[misc]

    def _get_conversation(self, conversation_id: SnowflakeID) -> Optional[Dict]:
        return super()._get_conversation(conversation_id)  # type: ignore[misc]

    def _get_unique_emoji_count(self, message_id: SnowflakeID) -> int:
        return super()._get_unique_emoji_count(message_id)  # type: ignore[misc]

    def _get_channel_for_conversation(
        self, conversation_id: SnowflakeID
    ) -> Optional[Dict]:
        return super()._get_channel_for_conversation(conversation_id)  # type: ignore[misc]

    def _row_to_reaction(self, row) -> Reaction:
        return super()._row_to_reaction(row)  # type: ignore[misc]

    def _row_to_custom_emoji(self, row) -> CustomEmoji:
        return super()._row_to_custom_emoji(row)  # type: ignore[misc]

    def _is_participant(
        self, conversation_id: SnowflakeID, user_id: SnowflakeID
    ) -> bool:
        return super()._is_participant(conversation_id, user_id)  # type: ignore[misc]

    def _check_server_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID] = None,
    ) -> bool:
        return super()._check_server_permission(user_id, server_id, channel_id)  # type: ignore[misc]

    def _validate_emoji(self, emoji: str) -> tuple[bool, Optional[int], str]:
        return super()._validate_emoji(emoji)  # type: ignore[misc]

    def _validate_custom_emoji_for_server(
        self, custom_emoji_id: SnowflakeID, server_id: SnowflakeID
    ) -> bool:
        return super()._validate_custom_emoji_for_server(custom_emoji_id, server_id)  # type: ignore[misc]

    def _validate_emoji_name(self, name: str) -> str:
        return super()._validate_emoji_name(name)  # type: ignore[misc]

    def _check_emoji_limits(self, server_id: SnowflakeID, animated: bool) -> None:
        super()._check_emoji_limits(server_id, animated)  # type: ignore[misc]
