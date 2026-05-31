"""
Embed URL preview mixin - Link preview generation.
"""

from typing import Any, Dict, Optional
from urllib.parse import urlparse

import utils.logger as logger
from src.core.base import SnowflakeID
from src.core.embeds.exceptions import (
    EmbedValidationError,
    InvalidUrlError,
    PreviewRateLimitError,
)
from src.core.embeds.models import Embed, EmbedType
from .protocol import EmbedManagerProtocol


class EmbedURLPreviewMixin(EmbedManagerProtocol):
    """
    Mixin providing URL preview generation via OpenGraph/Twitter Card scraping.

    Depends on:
    - _link_preview_service from EmbedManagerBase
    - validate_url from validator module
    - _validate_embed_data from EmbedValidationMixin
    - attach_embed_to_message from EmbedAttachmentMixin
    - get_embed from EmbedManagerBase
    - _get_timestamp from BaseManager
    - _generate_id from BaseManager
    """

    _link_preview_service: Any
    _db: Any
    _validate_embed_data: Any

    def _scrape_url_metadata(self, url: str) -> Dict[str, Any]:
        """Scrape URL metadata for previews.

        This method exists primarily as an override point for unit tests.
        In production it delegates to the hardened LinkPreviewService.
        """
        preview = self._link_preview_service.generate_preview(0, url)
        parsed = urlparse(url)
        host = parsed.hostname or parsed.netloc
        site_url = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else None

        return {
            "type": preview.embed_type,
            "title": preview.title,
            "description": preview.description,
            "site_name": preview.site_name or host,
            "site_url": site_url,
            "image": preview.image_url,
            "image_width": None,
            "image_height": None,
            "author": preview.author,
        }

    def create_url_preview(
        self, user_id: SnowflakeID, url: str, message_id: Optional[SnowflakeID] = None
    ) -> Embed:
        """
        Create a URL preview embed from OpenGraph/Twitter Card metadata.

        Uses the secure LinkPreviewService which provides:
        - SSRF protection (private IP blocking, DNS rebinding prevention)
        - Rate limiting per user
        - Preview caching
        - Image proxying (prevents IP leakage)
        - Redirect chain validation

        Args:
            user_id: ID of user creating preview
            url: URL to create preview for
            message_id: Optional message to attach preview to

        Returns:
            Created Embed

        Raises:
            InvalidUrlError: Invalid URL
            PreviewRateLimitError: Rate limit exceeded
        """
        from src.core.embeds.validator import validate_url

        validated_url = validate_url(url, "url")

        try:
            scraped = self._scrape_url_metadata(validated_url)
        except RuntimeError as e:
            if "rate limit" in str(e).lower():
                raise PreviewRateLimitError(str(e))
            raise
        except ValueError as e:
            raise InvalidUrlError(str(e))

        embed_type_str = (scraped.get("type") or "link").lower()
        title = scraped.get("title")
        description = scraped.get("description")
        site_name = scraped.get("site_name")
        site_url = scraped.get("site_url")
        image_url = scraped.get("image")
        author = scraped.get("author")

        embed_data = {
            "title": title,
            "description": description,
            "url": validated_url,
            "image": {"url": image_url} if image_url else None,
            "provider": {"name": site_name, "url": site_url} if site_name else None,
            "author": {"name": author} if author else None,
            "fields": [],
        }

        validation = self._validate_embed_data(embed_data)
        if not validation.valid:
            raise EmbedValidationError(
                "URL preview metadata validation failed", validation.issues
            )

        sanitized = validation.sanitized_data
        assert sanitized is not None

        now = self._get_timestamp()
        embed_id = self._generate_id()

        embed_type = EmbedType.LINK
        if embed_type_str == "video":
            embed_type = EmbedType.VIDEO
        elif embed_type_str == "article":
            embed_type = EmbedType.ARTICLE

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
                validated_url,
                None,
                None,
                None,
                None,
                sanitized.get("image", {}).get("url")
                if sanitized.get("image")
                else None,
                sanitized.get("image", {}).get("width")
                if sanitized.get("image")
                else None,
                sanitized.get("image", {}).get("height")
                if sanitized.get("image")
                else None,
                None,
                None,
                None,
                sanitized.get("author", {}).get("name")
                if sanitized.get("author")
                else None,
                None,
                None,
                sanitized.get("provider", {}).get("name")
                if sanitized.get("provider")
                else None,
                sanitized.get("provider", {}).get("url")
                if sanitized.get("provider")
                else None,
                user_id,
                now,
                1,
                validated_url,
            ),
        )

        logger.debug(f"URL preview embed {embed_id} created for {validated_url}")

        embed = self.get_embed(embed_id)
        assert embed is not None

        if message_id:
            self.attach_embed_to_message(user_id, message_id, embed_id)

        return embed

    def parse_url_metadata(self, url: str) -> Dict[str, Any]:
        """
        Parse URL metadata (OpenGraph/Twitter Card) without creating embed.

        Uses the secure LinkPreviewService for safe fetching.

        Args:
            url: URL to parse

        Returns:
            Dict with metadata (title, description, image, etc.)
        """
        from src.core.embeds.validator import validate_url

        validated_url = validate_url(url, "url")

        try:
            metadata = self._scrape_url_metadata(validated_url)
            metadata["url"] = validated_url
            return metadata
        except Exception as e:
            parsed = urlparse(validated_url)
            logger.warning(f"Failed to parse URL metadata for {validated_url}: {e}")
            return {
                "url": validated_url,
                "site_name": parsed.netloc,
                "site_url": f"{parsed.scheme}://{parsed.netloc}",
                "type": "link",
            }
