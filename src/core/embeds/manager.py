"""
Embed manager - Core business logic for embed operations.

Handles creating, updating, and managing embeds with proper
validation, permission checks, and database interactions.
"""

import time
import socket
import ipaddress
from html.parser import HTMLParser
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import urlparse, urljoin

import utils.config as config
import utils.logger as logger
from src.utils.encryption import generate_snowflake_id

from .models import (
    Embed,
    EmbedField,
    EmbedAuthor,
    EmbedFooter,
    EmbedImage,
    EmbedThumbnail,
    EmbedProvider,
    EmbedType,
)
from .exceptions import (
    EmbedNotFoundError,
    EmbedValidationError,
    EmbedLimitError,
    MessageNotFoundError,
    PermissionDeniedError,
)
from .schema import create_tables
from .validator import (
    validate_embed_data,
    validate_url,
    sanitize_content,
    MAX_EMBEDS_PER_MESSAGE,
    MAX_FIELDS,
    TOTAL_CHAR_LIMIT,
)


class EmbedManager:
    """Core embed manager handling all operations."""

    def __init__(self, db, messaging_module=None, servers_module=None):
        """
        Initialize the embed manager.

        Args:
            db: Database instance (must be connected)
            messaging_module: Messaging module for message access
            servers_module: Servers module for permission checks
        """
        self._db = db
        self._messaging = messaging_module
        self._servers = servers_module
        self._config = self._load_config()

        create_tables(db)

        logger.info("Embed module initialized")

    def _load_config(self) -> Dict[str, Any]:
        """Load embed configuration."""
        defaults = {
            "max_embeds_per_message": MAX_EMBEDS_PER_MESSAGE,
            "max_fields_per_embed": MAX_FIELDS,
            "total_char_limit": TOTAL_CHAR_LIMIT,
        }

        embeds_config = config.get("embeds", {})
        return {**defaults, **embeds_config}

    def _get_timestamp(self) -> int:
        """Get current timestamp in milliseconds."""
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        """Generate a new Snowflake ID."""
        return generate_snowflake_id()

    def _get_message(self, message_id: int) -> Optional[Dict]:
        """Get message from database."""
        return self._db.fetch_one(
            "SELECT * FROM msg_messages WHERE id = ? AND deleted = 0",
            (message_id,)
        )

    def _is_participant(self, conversation_id: int, user_id: int) -> bool:
        """Check if user is a participant in conversation."""
        row = self._db.fetch_one(
            "SELECT 1 FROM msg_participants WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, user_id)
        )
        return row is not None

    def _get_channel_for_conversation(self, conversation_id: int) -> Optional[Dict]:
        """Get server channel if conversation is a channel."""
        return self._db.fetch_one(
            "SELECT * FROM srv_channels WHERE conversation_id = ?",
            (conversation_id,)
        )

    def _check_embed_links_permission(self, user_id: int, server_id: int, channel_id: Optional[int] = None) -> bool:
        """Check if user has embed_links permission in server."""
        if not self._servers:
            return True
        return self._servers.has_permission(user_id, server_id, "messages.embed_links", channel_id)

    def create_embed(
        self,
        user_id: int,
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
        embed_type: EmbedType = EmbedType.RICH
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

        validation = validate_embed_data(embed_data)
        if not validation.valid:
            raise EmbedValidationError(
                "Embed validation failed",
                validation.issues
            )

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
                sanitized.get("footer", {}).get("text") if sanitized.get("footer") else None,
                sanitized.get("footer", {}).get("icon_url") if sanitized.get("footer") else None,
                sanitized.get("image", {}).get("url") if sanitized.get("image") else None,
                sanitized.get("image", {}).get("width") if sanitized.get("image") else None,
                sanitized.get("image", {}).get("height") if sanitized.get("image") else None,
                sanitized.get("thumbnail", {}).get("url") if sanitized.get("thumbnail") else None,
                sanitized.get("thumbnail", {}).get("width") if sanitized.get("thumbnail") else None,
                sanitized.get("thumbnail", {}).get("height") if sanitized.get("thumbnail") else None,
                sanitized.get("author", {}).get("name") if sanitized.get("author") else None,
                sanitized.get("author", {}).get("url") if sanitized.get("author") else None,
                sanitized.get("author", {}).get("icon_url") if sanitized.get("author") else None,
                sanitized.get("provider", {}).get("name") if sanitized.get("provider") else None,
                sanitized.get("provider", {}).get("url") if sanitized.get("provider") else None,
                user_id,
                now,
                0,
                None
            )
        )

        # Insert fields
        for i, field in enumerate(sanitized.get("fields", [])):
            field_id = self._generate_id()
            self._db.execute(
                """INSERT INTO embed_fields (id, embed_id, name, value, inline, position)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (field_id, embed_id, field["name"], field["value"], 1 if field.get("inline") else 0, i)
            )

        logger.debug(f"Embed {embed_id} created by user {user_id}")

        result = self.get_embed(embed_id)
        assert result is not None
        return result

    def get_embed(self, embed_id: int) -> Optional[Embed]:
        """Get an embed by ID."""
        row = self._db.fetch_one(
            "SELECT * FROM embed_embeds WHERE id = ?",
            (embed_id,)
        )

        if not row:
            return None

        return self._row_to_embed(row)

    def update_embed(
        self,
        user_id: int,
        embed_id: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        url: Optional[str] = None,
        timestamp: Optional[str] = None,
        color: Optional[str] = None,
        footer: Optional[Dict[str, Any]] = None,
        image: Optional[Dict[str, Any]] = None,
        thumbnail: Optional[Dict[str, Any]] = None,
        author: Optional[Dict[str, Any]] = None,
        fields: Optional[List[Dict[str, Any]]] = None
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

        # Build update data, keeping existing values for None params
        embed_data = {
            "title": title if title is not None else embed.title,
            "description": description if description is not None else embed.description,
            "url": url if url is not None else embed.url,
            "timestamp": timestamp if timestamp is not None else embed.timestamp,
            "color": color if color is not None else embed.color,
            "footer": footer if footer is not None else (
                {"text": embed.footer.text, "icon_url": embed.footer.icon_url} if embed.footer else None
            ),
            "image": image if image is not None else (
                {"url": embed.image.url, "width": embed.image.width, "height": embed.image.height} if embed.image else None
            ),
            "thumbnail": thumbnail if thumbnail is not None else (
                {"url": embed.thumbnail.url, "width": embed.thumbnail.width, "height": embed.thumbnail.height} if embed.thumbnail else None
            ),
            "author": author if author is not None else (
                {"name": embed.author.name, "url": embed.author.url, "icon_url": embed.author.icon_url} if embed.author else None
            ),
            "fields": fields if fields is not None else [
                {"name": f.name, "value": f.value, "inline": f.inline} for f in embed.fields
            ],
        }

        validation = validate_embed_data(embed_data)
        if not validation.valid:
            raise EmbedValidationError(
                "Embed validation failed",
                validation.issues
            )

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
                sanitized.get("footer", {}).get("text") if sanitized.get("footer") else None,
                sanitized.get("footer", {}).get("icon_url") if sanitized.get("footer") else None,
                sanitized.get("image", {}).get("url") if sanitized.get("image") else None,
                sanitized.get("image", {}).get("width") if sanitized.get("image") else None,
                sanitized.get("image", {}).get("height") if sanitized.get("image") else None,
                sanitized.get("thumbnail", {}).get("url") if sanitized.get("thumbnail") else None,
                sanitized.get("thumbnail", {}).get("width") if sanitized.get("thumbnail") else None,
                sanitized.get("thumbnail", {}).get("height") if sanitized.get("thumbnail") else None,
                sanitized.get("author", {}).get("name") if sanitized.get("author") else None,
                sanitized.get("author", {}).get("url") if sanitized.get("author") else None,
                sanitized.get("author", {}).get("icon_url") if sanitized.get("author") else None,
                embed_id
            )
        )

        # Update fields - delete existing and re-insert
        self._db.execute("DELETE FROM embed_fields WHERE embed_id = ?", (embed_id,))

        for i, field in enumerate(sanitized.get("fields", [])):
            field_id = self._generate_id()
            self._db.execute(
                """INSERT INTO embed_fields (id, embed_id, name, value, inline, position)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (field_id, embed_id, field["name"], field["value"], 1 if field.get("inline") else 0, i)
            )

        logger.debug(f"Embed {embed_id} updated by user {user_id}")

        result = self.get_embed(embed_id)
        assert result is not None  # Should exist since we just updated it
        return result

    def delete_embed(self, user_id: int, embed_id: int) -> bool:
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

        # Delete fields first
        self._db.execute("DELETE FROM embed_fields WHERE embed_id = ?", (embed_id,))

        # Delete message associations
        self._db.execute("DELETE FROM embed_message_embeds WHERE embed_id = ?", (embed_id,))

        # Delete embed
        self._db.execute("DELETE FROM embed_embeds WHERE id = ?", (embed_id,))

        logger.debug(f"Embed {embed_id} deleted by user {user_id}")

        return True

    def attach_embed_to_message(
        self,
        user_id: int,
        message_id: int,
        embed_id: int,
        position: Optional[int] = None
    ) -> bool:
        """
        Attach an embed to a message.
        
        Args:
            user_id: ID of user attaching embed
            message_id: ID of message
            embed_id: ID of embed to attach
            position: Optional position (0-indexed)
            
        Returns:
            True if attached
            
        Raises:
            MessageNotFoundError: Message not found
            EmbedNotFoundError: Embed not found
            EmbedLimitError: Max embeds reached
            PermissionDeniedError: No permission
        """
        msg = self._get_message(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")

        if msg["author_id"] != user_id:
            raise PermissionDeniedError("Can only attach embeds to own messages")

        if not self._is_participant(msg["conversation_id"], user_id):
            raise MessageNotFoundError("Message not found")

        # Check server permission if applicable
        channel = self._get_channel_for_conversation(msg["conversation_id"])
        if channel:
            if not self._check_embed_links_permission(user_id, channel["server_id"], channel["id"]):
                raise PermissionDeniedError(
                    "Missing permission to embed links",
                    "messages.embed_links"
                )

        embed = self.get_embed(embed_id)
        if not embed:
            raise EmbedNotFoundError("Embed not found")

        # Check embed limit
        max_embeds = self._config.get("max_embeds_per_message", MAX_EMBEDS_PER_MESSAGE)
        current_count = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM embed_message_embeds WHERE message_id = ?",
            (message_id,)
        )
        count = current_count["count"] if current_count else 0

        if count >= max_embeds:
            raise EmbedLimitError(
                f"Message has reached maximum of {max_embeds} embeds",
                max_embeds,
                count
            )

        # Check if already attached
        existing = self._db.fetch_one(
            "SELECT 1 FROM embed_message_embeds WHERE message_id = ? AND embed_id = ?",
            (message_id, embed_id)
        )
        if existing:
            return True  # Already attached

        # Determine position
        if position is None:
            position = count
        else:
            # Shift existing embeds at or after this position
            self._db.execute(
                """UPDATE embed_message_embeds 
                   SET position = position + 1 
                   WHERE message_id = ? AND position >= ?""",
                (message_id, position)
            )

        now = self._get_timestamp()
        assoc_id = self._generate_id()

        self._db.execute(
            """INSERT INTO embed_message_embeds (id, message_id, embed_id, position, suppressed, created_at)
               VALUES (?, ?, ?, ?, 0, ?)""",
            (assoc_id, message_id, embed_id, position, now)
        )

        logger.debug(f"Embed {embed_id} attached to message {message_id}")

        return True

    def remove_embed_from_message(self, user_id: int, message_id: int, embed_id: int) -> bool:
        """
        Remove an embed from a message.
        
        Args:
            user_id: ID of user removing embed
            message_id: ID of message
            embed_id: ID of embed to remove
            
        Returns:
            True if removed
            
        Raises:
            MessageNotFoundError: Message not found
            PermissionDeniedError: No permission
        """
        msg = self._get_message(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")

        if msg["author_id"] != user_id:
            raise PermissionDeniedError("Can only remove embeds from own messages")

        self._db.execute(
            "DELETE FROM embed_message_embeds WHERE message_id = ? AND embed_id = ?",
            (message_id, embed_id)
        )

        logger.debug(f"Embed {embed_id} removed from message {message_id}")

        return True

    def get_message_embeds(self, user_id: int, message_id: int) -> List[Embed]:
        """
        Get all embeds attached to a message.
        
        Args:
            user_id: ID of user requesting
            message_id: ID of message
            
        Returns:
            List of Embed objects
            
        Raises:
            MessageNotFoundError: Message not found
        """
        msg = self._get_message(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")

        if not self._is_participant(msg["conversation_id"], user_id):
            raise MessageNotFoundError("Message not found")

        # Check if embeds are suppressed
        rows = self._db.fetch_all(
            """SELECT e.*, me.suppressed, me.position
               FROM embed_embeds e
               INNER JOIN embed_message_embeds me ON e.id = me.embed_id
               WHERE me.message_id = ? AND me.suppressed = 0
               ORDER BY me.position""",
            (message_id,)
        )

        return [self._row_to_embed(row) for row in rows]

    def suppress_embeds(self, user_id: int, message_id: int) -> bool:
        """
        Suppress (hide) all embeds on a message.
        
        Args:
            user_id: ID of user suppressing
            message_id: ID of message
            
        Returns:
            True if suppressed
            
        Raises:
            MessageNotFoundError: Message not found
            PermissionDeniedError: No permission
        """
        msg = self._get_message(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")

        if msg["author_id"] != user_id:
            raise PermissionDeniedError("Can only suppress embeds on own messages")

        self._db.execute(
            "UPDATE embed_message_embeds SET suppressed = 1 WHERE message_id = ?",
            (message_id,)
        )

        logger.debug(f"Embeds suppressed on message {message_id}")

        return True

    def unsuppress_embeds(self, user_id: int, message_id: int) -> bool:
        """
        Unsuppress (show) all embeds on a message.
        
        Args:
            user_id: ID of user unsuppressing
            message_id: ID of message
            
        Returns:
            True if unsuppressed
            
        Raises:
            MessageNotFoundError: Message not found
            PermissionDeniedError: No permission
        """
        msg = self._get_message(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")

        if msg["author_id"] != user_id:
            raise PermissionDeniedError("Can only unsuppress embeds on own messages")

        self._db.execute(
            "UPDATE embed_message_embeds SET suppressed = 0 WHERE message_id = ?",
            (message_id,)
        )

        logger.debug(f"Embeds unsuppressed on message {message_id}")

        return True

    def create_url_preview(
        self,
        user_id: int,
        url: str,
        message_id: Optional[int] = None
    ) -> Embed:
        """
        Create a URL preview embed from OpenGraph/Twitter Card metadata.
        
        This simulates parsing URL metadata for preview generation.
        In production, this would fetch and parse the actual URL.
        
        Args:
            user_id: ID of user creating preview
            url: URL to create preview for
            message_id: Optional message to attach preview to
            
        Returns:
            Created Embed
            
        Raises:
            InvalidUrlError: Invalid URL
        """
        validated_url = validate_url(url, "url")
        metadata = self.parse_url_metadata(validated_url)

        # Validate/sanitize scraped metadata using existing embed validation rules
        embed_data = {
            "title": metadata.get("title"),
            "description": metadata.get("description"),
            "url": validated_url,
            "image": {"url": metadata.get("image"), "width": metadata.get("image_width"), "height": metadata.get("image_height")}
            if metadata.get("image")
            else None,
            "provider": {"name": metadata.get("site_name"), "url": metadata.get("site_url")}
            if metadata.get("site_name") or metadata.get("site_url")
            else None,
            "author": {"name": metadata.get("author")}
            if metadata.get("author")
            else None,
            "fields": [],
        }

        validation = validate_embed_data(embed_data)
        if not validation.valid:
            raise EmbedValidationError("URL preview metadata validation failed", validation.issues)

        sanitized = validation.sanitized_data
        assert sanitized is not None

        now = self._get_timestamp()
        embed_id = self._generate_id()

        embed_type = EmbedType.LINK
        if metadata.get("type") == "video":
            embed_type = EmbedType.VIDEO
        elif metadata.get("type") == "article":
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
                sanitized.get("image", {}).get("url") if sanitized.get("image") else None,
                sanitized.get("image", {}).get("width") if sanitized.get("image") else None,
                sanitized.get("image", {}).get("height") if sanitized.get("image") else None,
                None,
                None,
                None,
                sanitized.get("author", {}).get("name") if sanitized.get("author") else None,
                None,
                None,
                sanitized.get("provider", {}).get("name") if sanitized.get("provider") else None,
                sanitized.get("provider", {}).get("url") if sanitized.get("provider") else None,
                user_id,
                now,
                1,
                validated_url
            )
        )

        logger.debug(f"URL preview embed {embed_id} created for {validated_url}")

        embed = self.get_embed(embed_id)
        assert embed is not None  # Should exist since we just created it

        if message_id:
            self.attach_embed_to_message(user_id, message_id, embed_id)

        return embed

    def parse_url_metadata(self, url: str) -> Dict[str, Any]:
        """
        Parse URL metadata (OpenGraph/Twitter Card) without creating embed.
        
        Args:
            url: URL to parse
            
        Returns:
            Dict with metadata (title, description, image, etc.)
        """
        validated_url = validate_url(url, "url")
        parsed = urlparse(validated_url)

        # Baseline metadata (even if scraping fails)
        domain = parsed.netloc.lower()
        metadata: Dict[str, Any] = {
            "url": validated_url,
            "site_name": domain,
            "site_url": f"{parsed.scheme}://{parsed.netloc}",
            "type": "link",
        }

        try:
            scraped = self._scrape_url_metadata(validated_url)
            metadata.update({k: v for k, v in scraped.items() if v is not None})
        except Exception as e:
            # Fail closed: we still return minimal metadata, but do not raise here.
            logger.warning(f"Failed to scrape URL metadata for {validated_url}: {e}")

        return metadata

    def _validate_external_preview_url(self, url: str) -> Tuple[str, str, str]:
        """Validate URL for preview scraping (SSRF protection).

        Returns:
            Tuple of (normalized_url, base_url, hostname)
        """
        validated_url = validate_url(url, "url")
        parsed = urlparse(validated_url)

        scheme = (parsed.scheme or "").lower()
        if scheme not in {"http", "https"}:
            raise ValueError("Only http/https URLs are allowed")

        hostname = parsed.hostname or ""
        if not hostname:
            raise ValueError("Invalid URL: missing hostname")

        hostname_lower = hostname.lower()
        blocked_hosts = {
            "localhost",
            "127.0.0.1",
            "::1",
            "0.0.0.0",
            "localhost.localdomain",
            "local",
        }
        if hostname_lower in blocked_hosts:
            raise ValueError("Access to localhost is not allowed")

        if hostname_lower.endswith(".local") or hostname_lower.endswith(".internal"):
            raise ValueError("Access to internal hosts is not allowed")

        try:
            addr_info = socket.getaddrinfo(hostname, None)
            for _, _, _, _, sockaddr in addr_info:
                ip = str(sockaddr[0])
                if self._is_private_ip(ip):
                    raise ValueError("Access to private IP addresses is not allowed")
        except socket.gaierror:
            # If DNS resolution fails, let the HTTP client error out.
            pass

        base_url = f"{parsed.scheme}://{parsed.netloc}"
        return validated_url, base_url, hostname

    def _is_private_ip(self, ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip)
            return (
                addr.is_private
                or addr.is_loopback
                or addr.is_link_local
                or addr.is_reserved
                or addr.is_multicast
            )
        except ValueError:
            return True

    class _MetaParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.title: Optional[str] = None
            self._in_title = False
            self._title_parts: List[str] = []
            self.meta: Dict[str, str] = {}

        def handle_starttag(self, tag: str, attrs):
            tag_lower = tag.lower()
            if tag_lower == "title":
                self._in_title = True
                return

            if tag_lower != "meta":
                return

            attr_map = {k.lower(): (v or "") for k, v in attrs if k}
            key = (attr_map.get("property") or attr_map.get("name") or "").strip().lower()
            content = (attr_map.get("content") or "").strip()
            if key and content and key not in self.meta:
                self.meta[key] = content

        def handle_endtag(self, tag: str):
            if tag.lower() == "title":
                self._in_title = False
                if self._title_parts and self.title is None:
                    self.title = "".join(self._title_parts).strip() or None

        def handle_data(self, data: str):
            if self._in_title and data:
                self._title_parts.append(data)

    def _scrape_url_metadata(self, url: str) -> Dict[str, Any]:
        """Fetch HTML and extract OpenGraph/Twitter metadata securely."""
        validated_url, base_url, _ = self._validate_external_preview_url(url)

        # Keep these defaults conservative; allow override via config if present.
        preview_cfg = self._config.get("url_preview", {}) if isinstance(self._config, dict) else {}
        timeout_seconds = int(preview_cfg.get("timeout_seconds", 8))
        max_html_bytes = int(preview_cfg.get("max_html_bytes", 512 * 1024))
        max_redirects = int(preview_cfg.get("max_redirects", 5))

        try:
            import httpx
        except ImportError as e:
            raise RuntimeError("httpx is required for URL preview scraping") from e

        headers = {
            "User-Agent": "PlexiChat/1.0 (+https://plexichat)",
            "Accept": "text/html,application/xhtml+xml",
        }

        with httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout_seconds, connect=min(5, timeout_seconds)),
            headers=headers,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            max_redirects=max_redirects,
        ) as client:
            with client.stream("GET", validated_url) as resp:
                resp.raise_for_status()

                content_type = (resp.headers.get("content-type") or "").lower()
                if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                    raise ValueError(f"Unsupported content-type for preview: {content_type}")

                chunks: List[bytes] = []
                total = 0
                for chunk in resp.iter_bytes():
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_html_bytes:
                        break
                    chunks.append(chunk)

        html_bytes = b"".join(chunks)
        # Best-effort decode; HTMLParser accepts str.
        html_text = html_bytes.decode("utf-8", errors="replace")

        parser = self._MetaParser()
        parser.feed(html_text)

        def pick(*keys: str) -> Optional[str]:
            for k in keys:
                v = parser.meta.get(k)
                if v:
                    return v
            return None

        og_type = pick("og:type")
        title = pick("og:title", "twitter:title") or parser.title
        description = pick("og:description", "twitter:description", "description")
        site_name = pick("og:site_name")
        image = pick(
            "og:image:secure_url",
            "og:image:url",
            "og:image",
            "twitter:image",
            "twitter:image:src",
        )

        if image:
            image = urljoin(base_url + "/", image)

        embed_type = "link"
        if og_type:
            og_type_lower = og_type.lower()
            if og_type_lower.startswith("video"):
                embed_type = "video"
            elif og_type_lower in {"article", "profile", "website"}:
                embed_type = "article" if og_type_lower == "article" else "link"

        return {
            "type": embed_type,
            "title": title,
            "description": description,
            "site_name": site_name,
            "site_url": base_url,
            "image": image,
            "image_width": None,
            "image_height": None,
            "author": pick("author", "article:author"),
        }

    def validate_embed_data(self, embed_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate embed data and return validation result.
        
        Args:
            embed_data: Embed data dictionary
            
        Returns:
            Dict with valid, issues, total_chars, sanitized_data
        """
        result = validate_embed_data(embed_data)
        return {
            "valid": result.valid,
            "issues": result.issues,
            "total_chars": result.total_chars,
            "sanitized_data": result.sanitized_data,
        }

    def sanitize_content(self, content: str) -> str:
        """
        Sanitize embed content (remove scripts, validate URLs).
        
        Args:
            content: Content to sanitize
            
        Returns:
            Sanitized content
        """
        return sanitize_content(content, "content")

    def _row_to_embed(self, row) -> Embed:
        """Convert database row to Embed."""
        # Get fields
        fields_rows = self._db.fetch_all(
            "SELECT * FROM embed_fields WHERE embed_id = ? ORDER BY position",
            (row["id"],)
        )

        fields = [
            EmbedField(
                name=f["name"],
                value=f["value"],
                inline=bool(f["inline"])
            )
            for f in fields_rows
        ]

        footer = None
        if row["footer_text"]:
            footer = EmbedFooter(
                text=row["footer_text"],
                icon_url=row["footer_icon_url"]
            )

        image = None
        if row["image_url"]:
            image = EmbedImage(
                url=row["image_url"],
                width=row["image_width"],
                height=row["image_height"]
            )

        thumbnail = None
        if row["thumbnail_url"]:
            thumbnail = EmbedThumbnail(
                url=row["thumbnail_url"],
                width=row["thumbnail_width"],
                height=row["thumbnail_height"]
            )

        author = None
        if row["author_name"]:
            author = EmbedAuthor(
                name=row["author_name"],
                url=row["author_url"],
                icon_url=row["author_icon_url"]
            )

        provider = None
        if row["provider_name"] or row["provider_url"]:
            provider = EmbedProvider(
                name=row["provider_name"],
                url=row["provider_url"]
            )

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
            source_url=row["source_url"]
        )
