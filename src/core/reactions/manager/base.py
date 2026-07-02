from typing import Optional, Dict, Any
import utils.config as config
import utils.logger as logger
from src.core.base import BaseManager, SnowflakeID
from ..models import (
    Reaction,
    CustomEmoji,
)
from .permissions import ReactionPermissionsMixin
from .validation import ReactionValidationMixin
from .reactions import ReactionOpsMixin
from .emojis import EmojiOpsMixin


class ReactionBase(BaseManager):
    def __init__(
        self,
        db,
        auth_module=None,
        messaging_module=None,
        servers_module=None,
        relationships_module=None,
        media_module=None,
    ):
        super().__init__(db, auth_module)
        self._messaging = messaging_module
        self._servers = servers_module
        self._relationships = relationships_module
        self._media = media_module
        self._config = self._load_config()
        logger.info("Reaction module initialized")

    def _load_config(self) -> Dict[str, Any]:
        defaults = {
            "max_reactions_per_message": 20,
            "max_users_per_reaction_page": 100,
            "max_emojis_per_server": 50,
            "max_animated_emojis_per_server": 50,
            "max_emoji_size": 262144,
            "emoji_allowed_formats": ["image/png", "image/gif", "image/webp"],
            "emoji_max_name_length": 32,
            "emoji_min_name_length": 2,
        }
        reactions_config = config.get("reactions", {})
        emojis_config = config.get("emojis", {})
        return {**defaults, **reactions_config, **emojis_config}

    def _get_message(self, message_id: SnowflakeID) -> Optional[Dict]:
        return self._db.fetch_one(
            "SELECT * FROM msg_messages WHERE id = ? AND deleted = 0", (message_id,)
        )

    def _get_conversation(self, conversation_id: SnowflakeID) -> Optional[Dict]:
        return self._db.fetch_one(
            "SELECT * FROM msg_conversations WHERE id = ? AND deleted = 0",
            (conversation_id,),
        )

    def _get_unique_emoji_count(self, message_id: SnowflakeID) -> int:
        row = self._db.fetch_one(
            "SELECT COUNT(DISTINCT emoji) as count FROM react_reactions WHERE message_id = ?",
            (message_id,),
        )
        return row["count"] if row else 0

    def _row_to_reaction(self, row) -> Reaction:
        return Reaction(
            id=row["id"],
            message_id=row["message_id"],
            user_id=row["user_id"],
            emoji=row["emoji"],
            is_custom=bool(row["is_custom"]),
            custom_emoji_id=row["custom_emoji_id"],
            created_at=row["created_at"],
        )

    def _row_to_custom_emoji(self, row) -> CustomEmoji:
        return CustomEmoji(
            id=row["id"],
            server_id=row["server_id"],
            name=row["name"],
            animated=bool(row["animated"]),
            url=row.get("url", "") or "",
            size=row.get("size", 0) or 0,
            width=row.get("width"),
            height=row.get("height"),
            created_by=row.get("created_by", 0) or 0,
            available=bool(row.get("available", 1)),
            created_at=row["created_at"],
            uploader_username=row.get("uploader_username"),
        )


class ReactionManager(
    ReactionBase,
    ReactionPermissionsMixin,
    ReactionValidationMixin,
    ReactionOpsMixin,
    EmojiOpsMixin,
):
    pass
