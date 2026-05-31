"""
Embed CRUD mixin - Create, update, and delete operations.
"""

from typing import Any, Dict, List, Optional

import utils.logger as logger
from src.core.base import SnowflakeID
from src.core.embeds.exceptions import (
    EmbedNotFoundError,
    EmbedValidationError,
    PermissionDeniedError,
)
from src.core.embeds.models import Embed, EmbedType
from .protocol import EmbedManagerProtocol


class EmbedCRUDMixin(EmbedManagerProtocol):
    """
    Mixin providing create, update, and delete operations for embeds.

    Depends on:
    - get_embed from EmbedManagerBase
    - _row_to_embed from EmbedManagerBase
    - _get_timestamp from BaseManager
    - _generate_id from BaseManager
    - validate_embed_data from EmbedValidationMixin
    """

    _db: Any
    _validate_embed_data: Any

    def create_embed(
        self,
        user_id: SnowflakeID,
        title: Optional[str] = None,
        description: Optional[str] = None,
        url: Optional[str] = None,
        timestamp: Optional[str] = None,
        color: Optional[str] = None,
        footer: Optional[Dict[str, Any]] = None,
        image: Optional[Dict[str, Any]] = None,
        thumbnail: Optional[Dict[str, Any]] = None,
        author: Optional[Dict[str, Any]] = None,
        fields: Optional[List[Dict[str, Any]]] = None,
        provider: Optional[Dict[str, Any]] = None,
        embed_type: EmbedType = EmbedType.RICH,
    ) -> Embed:
        """
        Create a new embed.

        Args:
            user_id: ID of user creating embed
            title: Embed title (max 256 chars)
            description: Embed description (max 4096 chars)
            url: URL for title hyperlink
            timestamp: ISO8601 timestamp
            color: Hex color code
            footer: Footer dict with text and optional icon_url
            image: Image dict with url and optional width/height
            thumbnail: Thumbnail dict with url and optional width/height
            author: Author dict with name and optional url/icon_url
            fields: List of field dicts with name/value/inline
            provider: Provider dict with name and optional url
            embed_type: Type of embed

        Returns:
            Created Embed

        Raises:
            EmbedValidationError: Invalid embed data
            InvalidUrlError: Invalid URL
            InvalidColorError: Invalid color
        """
        embed_data = {
            "title": title,
            "description": description,
            "url": url,
            "timestamp": timestamp,
            "color": color,
            "footer": footer,
            "image": image,
            "thumbnail": thumbnail,
            "author": author,
            "fields": fields or [],
            "provider": provider,
        }

        validation = self._validate_embed_data(embed_data)
        if not validation.valid:
            raise EmbedValidationError("Embed validation failed", validation.issues)

        sanitized = validation.sanitized_data
        assert sanitized is not None
        now = self._get_timestamp()
        embed_id = self._generate_id()

        self._db.execute(
            """INSERT INTO embed_embeds
               (id, embed_type, title, description, url, timestamp, color,
                footer_text, footer_icon_url, image_url, image_width, image_height,
                thumbnail_url, thumbnail_width, thumbnail_height,
                author_name, author_url, author_icon_url,
                provider_name, provider_url, created_by, created_at, is_url_preview, source_url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                embed_id,
                embed_type.value,
                sanitized.get("title"),
                sanitized.get("description"),
                sanitized.get("url"),
                sanitized.get("timestamp"),
                sanitized.get("color"),
                sanitized.get("footer", {}).get("text")
                if sanitized.get("footer")
                else None,
                sanitized.get("footer", {}).get("icon_url")
                if sanitized.get("footer")
                else None,
                sanitized.get("image", {}).get("url")
                if sanitized.get("image")
                else None,
                sanitized.get("image", {}).get("width")
                if sanitized.get("image")
                else None,
                sanitized.get("image", {}).get("height")
                if sanitized.get("image")
                else None,
                sanitized.get("thumbnail", {}).get("url")
                if sanitized.get("thumbnail")
                else None,
                sanitized.get("thumbnail", {}).get("width")
                if sanitized.get("thumbnail")
                else None,
                sanitized.get("thumbnail", {}).get("height")
                if sanitized.get("thumbnail")
                else None,
                sanitized.get("author", {}).get("name")
                if sanitized.get("author")
                else None,
                sanitized.get("author", {}).get("url")
                if sanitized.get("author")
                else None,
                sanitized.get("author", {}).get("icon_url")
                if sanitized.get("author")
                else None,
                sanitized.get("provider", {}).get("name")
                if sanitized.get("provider")
                else None,
                sanitized.get("provider", {}).get("url")
                if sanitized.get("provider")
                else None,
                user_id,
                now,
                0,
                None,
            ),
        )

        for i, field in enumerate(sanitized.get("fields", [])):
            field_id = self._generate_id()
            self._db.execute(
                """INSERT INTO embed_fields (id, embed_id, name, value, inline, position)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    field_id,
                    embed_id,
                    field["name"],
                    field["value"],
                    1 if field.get("inline") else 0,
                    i,
                ),
            )

        logger.debug(f"Embed {embed_id} created by user {user_id}")

        result = self.get_embed(embed_id)
        assert result is not None
        return result

    def update_embed(
        self,
        user_id: SnowflakeID,
        embed_id: SnowflakeID,
        title: Optional[str] = None,
        description: Optional[str] = None,
        url: Optional[str] = None,
        timestamp: Optional[str] = None,
        color: Optional[str] = None,
        footer: Optional[Dict[str, Any]] = None,
        image: Optional[Dict[str, Any]] = None,
        thumbnail: Optional[Dict[str, Any]] = None,
        author: Optional[Dict[str, Any]] = None,
        fields: Optional[List[Dict[str, Any]]] = None,
    ) -> Embed:
        """
        Update an existing embed.

        Args:
            user_id: ID of user updating embed
            embed_id: ID of embed to update
            Other args same as create_embed

        Returns:
            Updated Embed

        Raises:
            EmbedNotFoundError: Embed not found
            PermissionDeniedError: User cannot update this embed
            EmbedValidationError: Invalid embed data
        """
        embed = self.get_embed(embed_id)
        if not embed:
            raise EmbedNotFoundError("Embed not found")

        if embed.created_by != user_id:
            raise PermissionDeniedError("Can only update own embeds")

        embed_data = {
            "title": title if title is not None else embed.title,
            "description": description
            if description is not None
            else embed.description,
            "url": url if url is not None else embed.url,
            "timestamp": timestamp if timestamp is not None else embed.timestamp,
            "color": color if color is not None else embed.color,
            "footer": footer
            if footer is not None
            else (
                {"text": embed.footer.text, "icon_url": embed.footer.icon_url}
                if embed.footer
                else None
            ),
            "image": image
            if image is not None
            else (
                {
                    "url": embed.image.url,
                    "width": embed.image.width,
                    "height": embed.image.height,
                }
                if embed.image
                else None
            ),
            "thumbnail": thumbnail
            if thumbnail is not None
            else (
                {
                    "url": embed.thumbnail.url,
                    "width": embed.thumbnail.width,
                    "height": embed.thumbnail.height,
                }
                if embed.thumbnail
                else None
            ),
            "author": author
            if author is not None
            else (
                {
                    "name": embed.author.name,
                    "url": embed.author.url,
                    "icon_url": embed.author.icon_url,
                }
                if embed.author
                else None
            ),
            "fields": fields
            if fields is not None
            else [
                {"name": f.name, "value": f.value, "inline": f.inline}
                for f in embed.fields
            ],
        }

        validation = self._validate_embed_data(embed_data)
        if not validation.valid:
            raise EmbedValidationError("Embed validation failed", validation.issues)

        sanitized = validation.sanitized_data
        assert sanitized is not None

        self._db.execute(
            """UPDATE embed_embeds SET
               title = ?, description = ?, url = ?, timestamp = ?, color = ?,
               footer_text = ?, footer_icon_url = ?,
               image_url = ?, image_width = ?, image_height = ?,
               thumbnail_url = ?, thumbnail_width = ?, thumbnail_height = ?,
               author_name = ?, author_url = ?, author_icon_url = ?
               WHERE id = ?""",
            (
                sanitized.get("title"),
                sanitized.get("description"),
                sanitized.get("url"),
                sanitized.get("timestamp"),
                sanitized.get("color"),
                sanitized.get("footer", {}).get("text")
                if sanitized.get("footer")
                else None,
                sanitized.get("footer", {}).get("icon_url")
                if sanitized.get("footer")
                else None,
                sanitized.get("image", {}).get("url")
                if sanitized.get("image")
                else None,
                sanitized.get("image", {}).get("width")
                if sanitized.get("image")
                else None,
                sanitized.get("image", {}).get("height")
                if sanitized.get("image")
                else None,
                sanitized.get("thumbnail", {}).get("url")
                if sanitized.get("thumbnail")
                else None,
                sanitized.get("thumbnail", {}).get("width")
                if sanitized.get("thumbnail")
                else None,
                sanitized.get("thumbnail", {}).get("height")
                if sanitized.get("thumbnail")
                else None,
                sanitized.get("author", {}).get("name")
                if sanitized.get("author")
                else None,
                sanitized.get("author", {}).get("url")
                if sanitized.get("author")
                else None,
                sanitized.get("author", {}).get("icon_url")
                if sanitized.get("author")
                else None,
                embed_id,
            ),
        )

        self._db.execute("DELETE FROM embed_fields WHERE embed_id = ?", (embed_id,))

        for i, field in enumerate(sanitized.get("fields", [])):
            field_id = self._generate_id()
            self._db.execute(
                """INSERT INTO embed_fields (id, embed_id, name, value, inline, position)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    field_id,
                    embed_id,
                    field["name"],
                    field["value"],
                    1 if field.get("inline") else 0,
                    i,
                ),
            )

        logger.debug(f"Embed {embed_id} updated by user {user_id}")

        result = self.get_embed(embed_id)
        assert result is not None
        return result

    def delete_embed(self, user_id: SnowflakeID, embed_id: SnowflakeID) -> bool:
        """
        Delete an embed.

        Args:
            user_id: ID of user deleting embed
            embed_id: ID of embed to delete

        Returns:
            True if deleted

        Raises:
            EmbedNotFoundError: Embed not found
            PermissionDeniedError: User cannot delete this embed
        """
        embed = self.get_embed(embed_id)
        if not embed:
            raise EmbedNotFoundError("Embed not found")

        if embed.created_by != user_id:
            raise PermissionDeniedError("Can only delete own embeds")

        self._db.execute("DELETE FROM embed_fields WHERE embed_id = ?", (embed_id,))

        self._db.execute(
            "DELETE FROM embed_message_embeds WHERE embed_id = ?", (embed_id,)
        )

        self._db.execute("DELETE FROM embed_embeds WHERE id = ?", (embed_id,))

        logger.debug(f"Embed {embed_id} deleted by user {user_id}")

        return True
