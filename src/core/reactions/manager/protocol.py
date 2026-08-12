from typing import Any, Dict, Optional, cast

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
        return cast(ReactionProtocol, super())._get_timestamp()

    def _generate_id(self) -> int:
        return cast(ReactionProtocol, super())._generate_id()

    def _user_exists(self, user_id: SnowflakeID) -> bool:
        return cast(ReactionProtocol, super())._user_exists(user_id)

    def _get_message(self, message_id: SnowflakeID) -> Optional[Dict]:
        return cast(ReactionProtocol, super())._get_message(message_id)

    def _get_conversation(self, conversation_id: SnowflakeID) -> Optional[Dict]:
        return cast(ReactionProtocol, super())._get_conversation(conversation_id)

    def _get_unique_emoji_count(self, message_id: SnowflakeID) -> int:
        return cast(ReactionProtocol, super())._get_unique_emoji_count(message_id)

    def _is_unique_violation(self, e: Exception) -> bool:
        return cast(ReactionProtocol, super())._is_unique_violation(e)

    def _get_channel_for_conversation(
        self, conversation_id: SnowflakeID
    ) -> Optional[Dict]:
        return cast(ReactionProtocol, super())._get_channel_for_conversation(
            conversation_id
        )

    def _row_to_reaction(self, row) -> Reaction:
        return cast(ReactionProtocol, super())._row_to_reaction(row)

    def _row_to_custom_emoji(self, row) -> CustomEmoji:
        return cast(ReactionProtocol, super())._row_to_custom_emoji(row)

    def _is_participant(
        self, conversation_id: SnowflakeID, user_id: SnowflakeID
    ) -> bool:
        return cast(ReactionProtocol, super())._is_participant(conversation_id, user_id)

    def _check_server_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID] = None,
    ) -> bool:
        return cast(ReactionProtocol, super())._check_server_permission(
            user_id, server_id, channel_id
        )

    def _validate_emoji(self, emoji: str) -> tuple[bool, Optional[int], str]:
        return cast(ReactionProtocol, super())._validate_emoji(emoji)

    def _validate_custom_emoji_exists(self, custom_emoji_id: SnowflakeID) -> bool:
        return cast(ReactionProtocol, super())._validate_custom_emoji_exists(
            custom_emoji_id
        )

    def _validate_custom_emoji_for_server(
        self, custom_emoji_id: SnowflakeID, server_id: SnowflakeID
    ) -> bool:
        return cast(ReactionProtocol, super())._validate_custom_emoji_for_server(
            custom_emoji_id, server_id
        )

    def _validate_emoji_name(self, name: str) -> str:
        return cast(ReactionProtocol, super())._validate_emoji_name(name)

    def _check_emoji_limits(self, server_id: SnowflakeID, animated: bool) -> None:
        cast(ReactionProtocol, super())._check_emoji_limits(server_id, animated)
