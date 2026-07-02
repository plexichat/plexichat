"""
Embed manager base - Core infrastructure for embed operations.

Provides EmbedManagerBase with shared utility methods, row conversion,
and permission-checking helpers used across all mixins.
"""

from typing import TYPE_CHECKING, Any, Dict, Optional

import utils.config as config
import utils.logger as logger
from src.core.base import BaseManager, SnowflakeID
from src.utils.security import URLValidator
from src.core.embeds.link_preview import LinkPreviewService

if TYPE_CHECKING:
    from src.core.embeds.models import Embed


class EmbedManagerBase(BaseManager):
    """
    Base embed manager providing core infrastructure.

    This class provides fundamental operations that all other mixins depend on:
    - Embed retrieval and row conversion
    - Message and participant lookups
    - Permission checking helpers
    - Link preview service
    """

    EmbedType: type
    EmbedSanitizationError: type

    _messaging: Any
    _servers: Any
    _media_proxy: Any
    _url_validator: Any
    _link_preview_service: Any
    _config: Dict[str, Any]

    def __init__(
        self,
        db: Any,
        messaging_module: Any = None,
        servers_module: Any = None,
        media_proxy: Any = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the embed manager base.

        Args:
            db: Database instance (must be connected)
            messaging_module: Optional messaging module for message access
            servers_module: Optional servers module for permission checks
            media_proxy: Optional media proxy for image caching
        """
        super().__init__(db, **kwargs)
        self._messaging = messaging_module
        self._servers = servers_module
        self._media_proxy = media_proxy
        self._url_validator = URLValidator()
        self._link_preview_service = LinkPreviewService(db, media_proxy)
        self._config = self._load_config()

        logger.info("Embed module initialized")

    def _load_config(self) -> Dict[str, Any]:
        """Load embed configuration."""
        from src.core.embeds.validator import (
            MAX_EMBEDS_PER_MESSAGE,
            MAX_FIELDS,
            TOTAL_CHAR_LIMIT,
        )

        defaults: Dict[str, Any] = {
            "max_embeds_per_message": MAX_EMBEDS_PER_MESSAGE,
            "max_fields_per_embed": MAX_FIELDS,
            "total_char_limit": TOTAL_CHAR_LIMIT,
        }

        embeds_config = config.get("embeds", {})
        return {**defaults, **embeds_config}

    def _row_to_embed(self, row: Dict[str, Any]) -> "Embed":
        """Convert database row to Embed."""
        from src.core.embeds.models import (
            Embed,
            EmbedField,
            EmbedFooter,
            EmbedImage,
            EmbedThumbnail,
            EmbedAuthor,
            EmbedProvider,
            EmbedType,
        )

        fields_rows = self._db.fetch_all(
            "SELECT * FROM embed_fields WHERE embed_id = ? ORDER BY position",
            (row["id"],),
        )

        fields = [
            EmbedField(name=f["name"], value=f["value"], inline=bool(f["inline"]))
            for f in fields_rows
        ]

        footer = None
        if row["footer_text"]:
            footer = EmbedFooter(
                text=row["footer_text"], icon_url=row["footer_icon_url"]
            )

        image = None
        if row["image_url"]:
            image = EmbedImage(
                url=row["image_url"],
                width=row["image_width"],
                height=row["image_height"],
            )

        thumbnail = None
        if row["thumbnail_url"]:
            thumbnail = EmbedThumbnail(
                url=row["thumbnail_url"],
                width=row["thumbnail_width"],
                height=row["thumbnail_height"],
            )

        author = None
        if row["author_name"]:
            author = EmbedAuthor(
                name=row["author_name"],
                url=row["author_url"],
                icon_url=row["author_icon_url"],
            )

        provider = None
        if row["provider_name"] or row["provider_url"]:
            provider = EmbedProvider(name=row["provider_name"], url=row["provider_url"])

        return Embed(
            id=row["id"],
            embed_type=EmbedType(row["embed_type"]),
            title=row["title"],
            description=row["description"],
            url=row["url"],
            timestamp=row["timestamp"],
            color=row["color"],
            footer=footer,
            image=image,
            thumbnail=thumbnail,
            author=author,
            provider=provider,
            fields=fields,
            created_by=row["created_by"],
            created_at=row["created_at"],
            is_url_preview=bool(row["is_url_preview"]),
            source_url=row["source_url"],
        )

    def get_embed(self, embed_id: SnowflakeID) -> Optional["Embed"]:
        """Get an embed by ID."""
        row = self._db.fetch_one("SELECT * FROM embed_embeds WHERE id = ?", (embed_id,))

        if not row:
            return None

        return self._row_to_embed(row)

    def _get_message(self, message_id: SnowflakeID) -> Optional[Dict[str, Any]]:
        """Get message from database."""
        return self._db.fetch_one(
            "SELECT * FROM msg_messages WHERE id = ? AND deleted = 0", (message_id,)
        )

    def _is_participant(
        self, conversation_id: SnowflakeID, user_id: SnowflakeID
    ) -> bool:
        """Check if user is a participant in conversation."""
        row = self._db.fetch_one(
            "SELECT 1 FROM msg_participants WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, user_id),
        )
        return row is not None

    def _get_channel_for_conversation(
        self, conversation_id: SnowflakeID
    ) -> Optional[Dict[str, Any]]:
        """Get server channel if conversation is a channel."""
        return self._db.fetch_one(
            "SELECT * FROM srv_channels WHERE conversation_id = ?", (conversation_id,)
        )

    def _check_embed_links_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID] = None,
    ) -> bool:
        """Check if user has embed_links permission in server."""
        if not self._servers:
            return True
        return self._servers.has_permission(
            user_id, server_id, "messages.embed_links", channel_id
        )
