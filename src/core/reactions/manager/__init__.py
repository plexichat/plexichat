"""
Reaction manager - Core business logic for reaction operations.

Handles adding, removing, and querying reactions with proper
validation, permission checks, and database interactions.
"""

import re
from typing import Optional, List, Dict, Any

import utils.config as config
import utils.logger as logger
from src.core.base import BaseManager, SnowflakeID

from ..models import (
    Reaction,
    ReactionCount,
    ReactionUser,
    MessageReactions,
    CustomEmoji,
)
from ..exceptions import (
    MessageNotFoundError,
    ReactionNotFoundError,
    ReactionExistsError,
    InvalidEmojiError,
    CustomEmojiNotFoundError,
    ReactionLimitError,
    PermissionDeniedError,
    EmojiLimitError,
    EmojiNameExistsError,
    InvalidEmojiNameError,
    EmojiFileSizeError,
    InvalidEmojiFileError,
)


CUSTOM_EMOJI_PATTERN = re.compile(r"^<a?:([a-zA-Z0-9_]+):(\d+)>$")


class ReactionManager(BaseManager):
    """Core reaction manager handling all operations."""

    def __init__(
        self,
        db,
        auth_module=None,
        messaging_module=None,
        servers_module=None,
        relationships_module=None,
        media_module=None,
    ):
        """
        Initialize the reaction manager.

        Args:
            db: Database instance (must be connected)
            auth_module: Auth module for user validation
            messaging_module: Messaging module for message access
            servers_module: Servers module for permission checks
            relationships_module: Relationships module for block filtering
            media_module: Media module for emoji image uploads
        """
        super().__init__(db, auth_module)
        self._messaging = messaging_module
        self._servers = servers_module
        self._relationships = relationships_module
        self._media = media_module
        self._config = self._load_config()

        self._migrate_emoji_table()

        logger.info("Reaction module initialized")

    def get_conversation_id_from_message(
        self, message_id: SnowflakeID
    ) -> Optional[SnowflakeID]:
        """Get conversation ID for a message."""
        row = self._db.fetch_one(
            "SELECT conversation_id FROM msg_messages WHERE id = ?", (message_id,)
        )
        return row["conversation_id"] if row else None

    def get_participant_ids(self, conversation_id: SnowflakeID) -> List[SnowflakeID]:
        """Get all participant IDs for a conversation, including server members if applicable."""
        # Get all participants in the conversation
        participant_rows = self._db.fetch_all(
            "SELECT user_id FROM msg_participants WHERE conversation_id = ?",
            (conversation_id,),
        )
        participant_ids = [row["user_id"] for row in participant_rows]

        # Also check if this is a server channel and get server members
        import json

        conv_row = self._db.fetch_one(
            "SELECT metadata FROM msg_conversations WHERE id = ?", (conversation_id,)
        )
        if conv_row and conv_row.get("metadata"):
            try:
                metadata = (
                    json.loads(conv_row["metadata"])
                    if isinstance(conv_row["metadata"], str)
                    else conv_row["metadata"]
                )
                server_id = (
                    metadata.get("server_id") if isinstance(metadata, dict) else None
                )
                if server_id:
                    member_rows = self._db.fetch_all(
                        "SELECT user_id FROM srv_members WHERE server_id = ?",
                        (server_id,),
                    )
                    for row in member_rows:
                        if row["user_id"] not in participant_ids:
                            participant_ids.append(row["user_id"])
            except (json.JSONDecodeError, TypeError) as e:
                logger.debug(
                    f"Failed to parse conversation metadata for reactions: {e}"
                )

        return participant_ids

    def _migrate_emoji_table(self):
        """Add new columns to emoji table if they don't exist."""
        try:
            self._db.execute("SELECT url FROM react_custom_emoji LIMIT 1")
        except Exception:
            logger.info("Migrating react_custom_emoji table with new columns")
            migrations = [
                "ALTER TABLE react_custom_emoji ADD COLUMN url TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE react_custom_emoji ADD COLUMN size INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE react_custom_emoji ADD COLUMN width INTEGER",
                "ALTER TABLE react_custom_emoji ADD COLUMN height INTEGER",
                "ALTER TABLE react_custom_emoji ADD COLUMN created_by INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE react_custom_emoji ADD COLUMN available INTEGER NOT NULL DEFAULT 1",
            ]
            for sql in migrations:
                try:
                    self._db.execute(sql)
                except Exception as e:
                    # Column might already exist, log at debug level
                    logger.debug(f"Migration step failed (possibly column exists): {e}")

    def _load_config(self) -> Dict[str, Any]:
        """Load reaction configuration."""
        defaults = {
            "max_reactions_per_message": 20,
            "max_users_per_reaction_page": 100,
            "max_emojis_per_server": 50,
            "max_animated_emojis_per_server": 50,
            "max_emoji_size": 262144,  # 256KB
            "emoji_allowed_formats": ["image/png", "image/gif", "image/webp"],
            "emoji_max_name_length": 32,
            "emoji_min_name_length": 2,
        }

        reactions_config = config.get("reactions", {})
        emojis_config = config.get("emojis", {})
        return {**defaults, **reactions_config, **emojis_config}

    def _get_message(self, message_id: SnowflakeID) -> Optional[Dict]:
        """Get message from database."""
        return self._db.fetch_one(
            "SELECT * FROM msg_messages WHERE id = ? AND deleted = 0", (message_id,)
        )

    def _get_conversation(self, conversation_id: SnowflakeID) -> Optional[Dict]:
        """Get conversation from database."""
        return self._db.fetch_one(
            "SELECT * FROM msg_conversations WHERE id = ? AND deleted = 0",
            (conversation_id,),
        )

    def _is_participant(
        self, conversation_id: SnowflakeID, user_id: SnowflakeID
    ) -> bool:
        """Check if user is a participant in conversation."""
        # First check direct participants table
        row = self._db.fetch_one(
            "SELECT 1 FROM msg_participants WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, user_id),
        )
        if row:
            return True

        # Check if this is a server channel conversation
        conv_row = self._db.fetch_one(
            "SELECT metadata FROM msg_conversations WHERE id = ?", (conversation_id,)
        )
        if conv_row:
            import json

            metadata_str = (
                conv_row["metadata"] if "metadata" in conv_row.keys() else None
            )
            if metadata_str:
                try:
                    metadata = (
                        json.loads(metadata_str)
                        if isinstance(metadata_str, str)
                        else metadata_str
                    )
                    server_id = (
                        metadata.get("server_id")
                        if isinstance(metadata, dict)
                        else None
                    )
                    if server_id:
                        # Check if user is a member of the server
                        member_row = self._db.fetch_one(
                            "SELECT 1 FROM srv_members WHERE server_id = ? AND user_id = ?",
                            (server_id, user_id),
                        )
                        return member_row is not None
                except (json.JSONDecodeError, TypeError):
                    pass

        return False

    def _get_channel_for_conversation(
        self, conversation_id: SnowflakeID
    ) -> Optional[Dict]:
        """Get server channel if conversation is a channel."""
        return self._db.fetch_one(
            "SELECT * FROM srv_channels WHERE conversation_id = ?", (conversation_id,)
        )

    def _check_server_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID] = None,
    ) -> bool:
        """Check if user has add_reactions permission in server."""
        if not self._servers:
            return True
        return self._servers.has_permission(
            user_id, server_id, "messages.add_reactions", channel_id
        )

    def _is_blocked_by_either(
        self, user_id: SnowflakeID, other_id: SnowflakeID
    ) -> bool:
        """Check if either user has blocked the other."""
        if not self._relationships:
            return False
        return self._relationships.is_blocked_by_either(user_id, other_id)

    def _validate_emoji(self, emoji: str) -> tuple:
        """
        Validate emoji and return (is_custom, custom_emoji_id, normalized_emoji).

        Returns:
            Tuple of (is_custom, custom_emoji_id, normalized_emoji)
        """
        if not emoji or not emoji.strip():
            raise InvalidEmojiError("Emoji cannot be empty")

        emoji = emoji.strip()

        custom_match = CUSTOM_EMOJI_PATTERN.match(emoji)
        if custom_match:
            emoji_id = int(custom_match.group(2))
            return (True, emoji_id, emoji)

        if emoji.startswith(":") or emoji.startswith("<"):
            raise InvalidEmojiError("Invalid emoji format")

        if len(emoji) > 32:
            raise InvalidEmojiError("Emoji is too long")

        # Validate Unicode emoji - check for valid emoji ranges
        # Reject zero-width chars, combining marks spam, RTL overrides
        if not self._is_valid_unicode_emoji(emoji):
            raise InvalidEmojiError("Invalid emoji characters")

        return (False, None, emoji)

    def _is_valid_unicode_emoji(self, text: str) -> bool:
        """
        Validate that text contains valid emoji characters.
        Rejects zero-width chars, excessive combining marks, control characters.
        """
        VALID_EMOJI_RANGES = [
            (0x1F000, 0x1FAFF),  # All major emoji blocks (Symbols, Emoticons, Transport, etc.)
            (0x1FB00, 0x1FBFF),  # Symbols for Legacy Computing
            (0x2600, 0x26FF),    # Miscellaneous Symbols
            (0x2700, 0x27BF),    # Dingbats
            (0x2300, 0x23FF),    # Miscellaneous Technical
            (0x2B50, 0x2B55),    # Stars, circles
            (0xFE00, 0xFE0F),    # Variation Selectors
            (0xE0020, 0xE007F),  # Tags (flag subdivisions)
        ]
        
        # Reject problematic characters
        REJECTED_CHARS = {
            0x200B,  # Zero-width space
            0x200C,  # Zero-width non-joiner
            0x200D,  # Zero-width joiner (when excessive)
            0x202E,  # Right-to-left override
            0x202D,  # Left-to-right override
            0xFEFF,  # Zero-width no-break space
        }
        
        if not text:
            return False
        
        # Check each character
        zwj_count = 0
        for char in text:
            code = ord(char)
            
            # Reject control chars
            if code < 0x20 and code not in (0x09, 0x0A, 0x0D):  # Allow tab, LF, CR
                return False
            
            # Count zero-width joiners (max 2-3 allowed for skin tones)
            if code == 0x200D:
                zwj_count += 1
                if zwj_count > 3:
                    return False
                continue
            
            # Reject override chars
            if code in REJECTED_CHARS:
                return False
            
            # Check if in valid emoji range
            in_valid_range = False
            for start, end in VALID_EMOJI_RANGES:
                if start <= code <= end:
                    in_valid_range = True
                    break
            
            # Also allow basic letters/numbers for text emoji variants
            if (0x30 <= code <= 0x39) or (0x41 <= code <= 0x5A) or (0x61 <= code <= 0x7A):
                in_valid_range = True
            
            # Allow some punctuation (for :name: style)
            if code in (0x3A, 0x2D, 0x5F):  # : - _
                in_valid_range = True

            # Allow keycap combining mark and copyright/registered symbols
            if code in (0x20E3, 0xA9, 0xAE):
                in_valid_range = True
            
            if not in_valid_range:
                return False
        
        return True

    def _validate_custom_emoji_for_server(
        self, custom_emoji_id: SnowflakeID, server_id: SnowflakeID
    ) -> bool:
        """
        Validate that custom emoji exists in the server and is available.
        Rejects soft-deleted emojis.
        """
        row = self._db.fetch_one(
            "SELECT 1 FROM react_custom_emoji WHERE id = ? AND server_id = ? AND available = 1",
            (custom_emoji_id, server_id),
        )
        return row is not None

    def _get_unique_emoji_count(self, message_id: SnowflakeID) -> int:
        """Get count of unique emoji on a message."""
        row = self._db.fetch_one(
            "SELECT COUNT(DISTINCT emoji) as count FROM react_reactions WHERE message_id = ?",
            (message_id,),
        )
        return row["count"] if row else 0

    def add_reaction(
        self, user_id: SnowflakeID, message_id: SnowflakeID, emoji: str
    ) -> Reaction:
        """
        Add a reaction to a message.

        Args:
            user_id: ID of user adding reaction
            message_id: ID of message to react to
            emoji: Unicode emoji or custom emoji string (<:name:id>)

        Returns:
            Created Reaction

        Raises:
            MessageNotFoundError: Message not found or not accessible
            InvalidEmojiError: Invalid emoji format
            CustomEmojiNotFoundError: Custom emoji not in server
            ReactionExistsError: User already reacted with this emoji
            ReactionLimitError: Max reactions reached
            PermissionDeniedError: No permission to add reactions
        """
        msg = self._get_message(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")

        if not self._is_participant(msg["conversation_id"], user_id):
            raise MessageNotFoundError("Message not found")

        channel = self._get_channel_for_conversation(msg["conversation_id"])
        if channel:
            if not self._check_server_permission(
                user_id, channel["server_id"], channel["id"]
            ):
                raise PermissionDeniedError(
                    "Missing permission to add reactions", "messages.add_reactions"
                )

        is_custom, custom_emoji_id, normalized_emoji = self._validate_emoji(emoji)

        if is_custom and channel:
            if not self._validate_custom_emoji_for_server(
                custom_emoji_id, channel["server_id"]
            ):
                raise CustomEmojiNotFoundError("Custom emoji not found in this server")

        existing = self._db.fetch_one(
            "SELECT 1 FROM react_reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
            (message_id, user_id, normalized_emoji),
        )
        if existing:
            raise ReactionExistsError("You have already reacted with this emoji")

        max_reactions = self._config.get("max_reactions_per_message", 20)
        max_user_reactions = self._config.get("max_unique_reactions_per_user", 50)

        # Use transaction to prevent race conditions on limit checks
        try:
            self._db.execute("BEGIN IMMEDIATE")
            
            # Re-check existence within transaction (may have changed)
            existing = self._db.fetch_one(
                "SELECT 1 FROM react_reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
                (message_id, user_id, normalized_emoji),
            )
            if existing:
                self._db.execute("ROLLBACK")
                raise ReactionExistsError("You have already reacted with this emoji")

            # Check unique count within transaction
            unique_count = self._get_unique_emoji_count(message_id)
            user_has_this_emoji = self._db.fetch_one(
                "SELECT 1 FROM react_reactions WHERE message_id = ? AND emoji = ?",
                (message_id, normalized_emoji),
            )

            if not user_has_this_emoji and unique_count >= max_reactions:
                self._db.execute("ROLLBACK")
                raise ReactionLimitError(
                    f"Message has reached maximum of {max_reactions} unique reactions",
                    max_reactions,
                    unique_count,
                )

            # Check user limit within transaction
            user_unique_count = self._db.fetch_one(
                "SELECT count(DISTINCT emoji) as count FROM react_reactions WHERE message_id = ? AND user_id = ?",
                (message_id, user_id),
            )["count"]

            if not user_has_this_emoji and user_unique_count >= max_user_reactions:
                self._db.execute("ROLLBACK")
                raise ReactionLimitError(
                    f"User has reached maximum of {max_user_reactions} unique reactions per message",
                    max_user_reactions,
                    user_unique_count,
                )

            # All checks passed, insert within transaction
            now = self._get_timestamp()
            reaction_id = self._generate_id()

            self._db.execute(
                """INSERT INTO react_reactions 
                   (id, message_id, user_id, emoji, is_custom, custom_emoji_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    reaction_id,
                    message_id,
                    user_id,
                    normalized_emoji,
                    1 if is_custom else 0,
                    custom_emoji_id,
                    now,
                ),
            )
            
            self._db.execute("COMMIT")
            
        except ReactionLimitError:
            self._db.execute("ROLLBACK")
            raise
        except ReactionExistsError:
            self._db.execute("ROLLBACK")
            raise
        except Exception as e:
            self._db.execute("ROLLBACK")
            raise

        logger.debug(
            f"Reaction {reaction_id} added to message {message_id} by user {user_id}"
        )

        result = self.get_reaction(reaction_id)
        assert result is not None  # Should exist since we just created it
        return result

    def remove_reaction(
        self, user_id: SnowflakeID, message_id: SnowflakeID, emoji: str
    ) -> bool:
        """
        Remove a reaction from a message.

        Args:
            user_id: ID of user removing reaction
            message_id: ID of message
            emoji: Emoji to remove

        Returns:
            True if removed

        Raises:
            MessageNotFoundError: Message not found
            ReactionNotFoundError: Reaction not found
        """
        msg = self._get_message(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")

        if not self._is_participant(msg["conversation_id"], user_id):
            raise MessageNotFoundError("Message not found")

        is_custom, custom_emoji_id, normalized_emoji = self._validate_emoji(emoji)

        any_reaction = self._db.fetch_one(
            "SELECT 1 FROM react_reactions WHERE message_id = ? AND emoji = ?",
            (message_id, normalized_emoji),
        )
        if not any_reaction:
            raise ReactionNotFoundError("Reaction not found")

        existing = self._db.fetch_one(
            "SELECT id FROM react_reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
            (message_id, user_id, normalized_emoji),
        )
        if not existing:
            raise PermissionDeniedError("Cannot remove reaction added by someone else")

        self._db.execute(
            "DELETE FROM react_reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
            (message_id, user_id, normalized_emoji),
        )

        logger.debug(f"Reaction removed from message {message_id} by user {user_id}")

        return True

    def remove_all_reactions(
        self, user_id: SnowflakeID, message_id: SnowflakeID
    ) -> int:
        """
        Remove all reactions from a message (moderator action).

        Args:
            user_id: ID of moderator
            message_id: ID of message

        Returns:
            Number of reactions removed

        Raises:
            MessageNotFoundError: Message not found
            PermissionDeniedError: No permission
        """
        msg = self._get_message(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")

        channel = self._get_channel_for_conversation(msg["conversation_id"])
        if channel:
            if not self._servers or not self._servers.has_permission(
                user_id, channel["server_id"], "messages.manage", channel["id"]
            ):
                raise PermissionDeniedError(
                    "Missing permission to manage messages", "messages.manage"
                )
        else:
            conv = self._get_conversation(msg["conversation_id"])
            if conv and conv["owner_id"] != user_id:
                participant = self._db.fetch_one(
                    "SELECT role FROM msg_participants WHERE conversation_id = ? AND user_id = ?",
                    (msg["conversation_id"], user_id),
                )
                if not participant or participant["role"] not in ("owner", "admin"):
                    raise PermissionDeniedError(
                        "Only conversation owner or admin can remove all reactions"
                    )

        count_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM react_reactions WHERE message_id = ?",
            (message_id,),
        )
        count = count_row["count"] if count_row else 0

        self._db.execute(
            "DELETE FROM react_reactions WHERE message_id = ?", (message_id,)
        )

        logger.debug(
            f"All reactions ({count}) removed from message {message_id} by user {user_id}"
        )

        return count

    def remove_all_reactions_for_emoji(
        self, user_id: int, message_id: int, emoji: str
    ) -> int:
        """
        Remove all reactions of a specific emoji from a message (moderator action).

        Args:
            user_id: ID of moderator
            message_id: ID of message
            emoji: Emoji to remove all reactions for

        Returns:
            Number of reactions removed

        Raises:
            MessageNotFoundError: Message not found
            PermissionDeniedError: No permission
        """
        msg = self._get_message(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")

        channel = self._get_channel_for_conversation(msg["conversation_id"])
        if channel:
            if not self._servers or not self._servers.has_permission(
                user_id, channel["server_id"], "messages.manage", channel["id"]
            ):
                raise PermissionDeniedError(
                    "Missing permission to manage messages", "messages.manage"
                )
        else:
            conv = self._get_conversation(msg["conversation_id"])
            if conv and conv["owner_id"] != user_id:
                participant = self._db.fetch_one(
                    "SELECT role FROM msg_participants WHERE conversation_id = ? AND user_id = ?",
                    (msg["conversation_id"], user_id),
                )
                if not participant or participant["role"] not in ("owner", "admin"):
                    raise PermissionDeniedError(
                        "Only conversation owner or admin can remove reactions"
                    )

        is_custom, custom_emoji_id, normalized_emoji = self._validate_emoji(emoji)

        count_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM react_reactions WHERE message_id = ? AND emoji = ?",
            (message_id, normalized_emoji),
        )
        count = count_row["count"] if count_row else 0

        self._db.execute(
            "DELETE FROM react_reactions WHERE message_id = ? AND emoji = ?",
            (message_id, normalized_emoji),
        )

        logger.debug(
            f"All reactions for emoji {emoji} ({count}) removed from message {message_id}"
        )

        return count

    def get_reaction(self, reaction_id: SnowflakeID) -> Optional[Reaction]:
        """Get a reaction by ID."""
        row = self._db.fetch_one(
            "SELECT * FROM react_reactions WHERE id = ?", (reaction_id,)
        )

        if not row:
            return None

        return self._row_to_reaction(row)

    def get_reactions(
        self, user_id: SnowflakeID, message_id: SnowflakeID
    ) -> MessageReactions:
        """
        Get all reactions on a message with counts.

        Args:
            user_id: ID of user requesting (for 'me' field and block filtering)
            message_id: ID of message

        Returns:
            MessageReactions with counts

        Raises:
            MessageNotFoundError: Message not found
        """
        msg = self._get_message(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")

        if not self._is_participant(msg["conversation_id"], user_id):
            raise MessageNotFoundError("Message not found")

        blocked_users = set()
        if self._relationships:
            blocked_ids = self._relationships.get_blocked_user_ids(user_id)
            blocked_users.update(blocked_ids)
            rows = self._db.fetch_all(
                "SELECT blocker_id FROM rel_blocked WHERE blocked_id = ?", (user_id,)
            )
            for row in rows:
                blocked_users.add(row["blocker_id"])

        # Build query with block filtering integrated
        query = """SELECT r.emoji, r.is_custom, r.custom_emoji_id, e.url, COUNT(*) as count,
                          MAX(CASE WHEN r.user_id = ? THEN 1 ELSE 0 END) as me
                   FROM react_reactions r
                   LEFT JOIN react_custom_emoji e ON r.custom_emoji_id = e.id
                   WHERE r.message_id = ?"""
        
        params = [user_id, message_id]
        
        if blocked_users:
            placeholders = ",".join("?" * len(blocked_users))
            query += f" AND r.user_id NOT IN ({placeholders})"
            params.extend(list(blocked_users))
            
        query += """ GROUP BY r.emoji, r.is_custom, r.custom_emoji_id, e.url
                     ORDER BY MIN(r.created_at)"""

        rows = self._db.fetch_all(query, tuple(params))

        reactions = []
        total = 0

        for row in rows:
            count = row["count"]
            if count > 0:
                reactions.append(
                    ReactionCount(
                        message_id=message_id,
                        emoji=row["emoji"],
                        count=count,
                        is_custom=bool(row["is_custom"]),
                        custom_emoji_id=row["custom_emoji_id"],
                        me=bool(row["me"]),
                        url=row.get("url"),
                    )
                )
                total += count

        return MessageReactions(
            message_id=message_id, reactions=reactions, total_count=total
        )

    def get_reaction_users(
        self,
        user_id: SnowflakeID,
        message_id: SnowflakeID,
        emoji: str,
        limit: int = 100,
        after_user_id: Optional[SnowflakeID] = None,
    ) -> List[ReactionUser]:
        """
        Get users who reacted with a specific emoji.

        Args:
            user_id: ID of user requesting (for block filtering)
            message_id: ID of message
            emoji: Emoji to get users for
            limit: Maximum users to return
            after_user_id: Cursor for pagination

        Returns:
            List of ReactionUser

        Raises:
            MessageNotFoundError: Message not found
        """
        msg = self._get_message(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")

        if not self._is_participant(msg["conversation_id"], user_id):
            raise MessageNotFoundError("Message not found")

        is_custom, custom_emoji_id, normalized_emoji = self._validate_emoji(emoji)

        max_per_page = self._config.get("max_users_per_reaction_page", 100)
        limit = min(limit, max_per_page)

        blocked_users = set()
        if self._relationships:
            blocked_ids = self._relationships.get_blocked_user_ids(user_id)
            blocked_users.update(blocked_ids)
            rows = self._db.fetch_all(
                "SELECT blocker_id FROM rel_blocked WHERE blocked_id = ?", (user_id,)
            )
            for row in rows:
                blocked_users.add(row["blocker_id"])

        query = """SELECT user_id, created_at FROM react_reactions 
                   WHERE message_id = ? AND emoji = ?"""
        params = [message_id, normalized_emoji]

        if blocked_users:
            query += " AND user_id NOT IN ({})".format(
                ",".join("?" * len(blocked_users))
            )
            params.extend(blocked_users)

        if after_user_id:
            query += " AND user_id > ?"
            params.append(after_user_id)

        query += " ORDER BY user_id LIMIT ?"
        params.append(limit)

        rows = self._db.fetch_all(query, tuple(params))

        return [
            ReactionUser(user_id=row["user_id"], reacted_at=row["created_at"])
            for row in rows
        ]

    def get_user_reactions(
        self, user_id: SnowflakeID, message_id: SnowflakeID
    ) -> List[Reaction]:
        """
        Get all reactions by a specific user on a message.

        Args:
            user_id: ID of user
            message_id: ID of message

        Returns:
            List of Reaction
        """
        msg = self._get_message(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")

        if not self._is_participant(msg["conversation_id"], user_id):
            raise MessageNotFoundError("Message not found")

        rows = self._db.fetch_all(
            "SELECT * FROM react_reactions WHERE message_id = ? AND user_id = ? ORDER BY created_at",
            (message_id, user_id),
        )

        return [self._row_to_reaction(row) for row in rows]

    def _validate_emoji_name(self, name: str) -> str:
        """Validate and normalize emoji name."""
        if not name or not name.strip():
            raise InvalidEmojiNameError("Emoji name cannot be empty")

        name = name.strip().lower()
        min_len = self._config.get("emoji_min_name_length", 2)
        max_len = self._config.get("emoji_max_name_length", 32)

        if len(name) < min_len or len(name) > max_len:
            raise InvalidEmojiNameError(
                f"Emoji name must be {min_len}-{max_len} characters"
            )

        if not re.match(r"^[a-z0-9_]+$", name):
            raise InvalidEmojiNameError(
                "Emoji name can only contain lowercase letters, numbers, and underscores"
            )

        return name

    def _check_emoji_limits(self, server_id: SnowflakeID, animated: bool) -> None:
        """Check if server has reached emoji limits."""
        if animated:
            max_count = self._config.get("max_animated_emojis_per_server", 50)
            row = self._db.fetch_one(
                "SELECT COUNT(*) as count FROM react_custom_emoji WHERE server_id = ? AND animated = 1",
                (server_id,),
            )
        else:
            max_count = self._config.get("max_emojis_per_server", 50)
            row = self._db.fetch_one(
                "SELECT COUNT(*) as count FROM react_custom_emoji WHERE server_id = ? AND animated = 0",
                (server_id,),
            )

        current = row["count"] if row else 0
        if current >= max_count:
            emoji_type = "animated emojis" if animated else "static emojis"
            raise EmojiLimitError(
                f"Server has reached maximum of {max_count} {emoji_type}",
                max_count,
                current,
            )

    def create_custom_emoji(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        name: str,
        image_data: bytes,
        content_type: str,
    ) -> CustomEmoji:
        """
        Create a custom emoji for a server with image upload.

        Args:
            user_id: ID of user creating emoji
            server_id: ID of server
            name: Emoji name (alphanumeric and underscores)
            image_data: Raw image bytes
            content_type: MIME type of image

        Returns:
            Created CustomEmoji

        Raises:
            PermissionDeniedError: No permission
            InvalidEmojiNameError: Invalid name
            EmojiLimitError: Server emoji limit reached
            EmojiFileSizeError: File too large
            InvalidEmojiFileError: Invalid file format
            EmojiNameExistsError: Name already taken
        """
        if self._servers:
            if not self._servers.has_permission(user_id, server_id, "server.manage"):
                raise PermissionDeniedError(
                    "Missing permission to manage server", "server.manage"
                )

        name = self._validate_emoji_name(name)

        # Check file size
        max_size = self._config.get("max_emoji_size", 262144)
        if len(image_data) > max_size:
            raise EmojiFileSizeError(
                f"Emoji file size exceeds {max_size // 1024}KB limit",
                max_size,
                len(image_data),
            )

        # Check content type
        allowed_formats = self._config.get(
            "emoji_allowed_formats", ["image/png", "image/gif", "image/webp"]
        )
        if content_type.lower() not in allowed_formats:
            raise InvalidEmojiFileError(
                f"Invalid format. Allowed: {', '.join(allowed_formats)}"
            )

        # Validate file integrity using PIL (magic number verification)
        try:
            from io import BytesIO
            from PIL import Image
            
            img = Image.open(BytesIO(image_data))
            # Verify image can be opened and has dimensions
            img.verify()
            width_hint = img.width
            height_hint = img.height
            
            # Sanity checks on dimensions (prevent huge images)
            if width_hint > 1024 or height_hint > 1024:
                raise InvalidEmojiFileError("Emoji image dimensions too large (max 1024x1024)")
            
        except Exception as e:
            # PIL couldn't validate - reject the file
            raise InvalidEmojiFileError(f"Invalid or corrupted image file: {str(e)}")

        # Detect if animated (GIF or animated WebP)
        animated = content_type.lower() == "image/gif"
        if content_type.lower() == "image/webp":
            # Check for animation in WebP
            animated = b"ANIM" in image_data[:100]

        # Check limits
        self._check_emoji_limits(server_id, animated)

        # Check name uniqueness - use transaction to prevent race condition
        try:
            self._db.execute("BEGIN IMMEDIATE")
            
            existing = self._db.fetch_one(
                "SELECT 1 FROM react_custom_emoji WHERE server_id = ? AND name = ?",
                (server_id, name),
            )
            if existing:
                self._db.execute("ROLLBACK")
                raise EmojiNameExistsError(
                    f"Emoji with name '{name}' already exists in this server"
                )

            # Upload image via media module or store directly
            url = ""
            width = None
            height = None

            if self._media:
                ext = "gif" if animated else content_type.split("/")[-1]
                filename = f"emoji_{name}.{ext}"
                try:
                    result = self._media.upload_file(
                        user_id, image_data, filename, content_type
                    )
                    url = result.url
                    if result.metadata:
                        width = result.metadata.get("width")
                        height = result.metadata.get("height")
                except Exception as e:
                    logger.error(f"Failed to upload emoji image: {e}")
                    self._db.execute("ROLLBACK")
                    raise InvalidEmojiFileError(f"Failed to upload emoji: {str(e)}")

            now = self._get_timestamp()
            emoji_id = self._generate_id()

            self._db.execute(
                """INSERT INTO react_custom_emoji 
                   (id, server_id, name, animated, url, size, width, height, created_by, available, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                (
                    emoji_id,
                    server_id,
                    name,
                    1 if animated else 0,
                    url,
                    len(image_data),
                    width,
                    height,
                    user_id,
                    now,
                ),
            )
            
            self._db.execute("COMMIT")
            
        except EmojiNameExistsError:
            self._db.execute("ROLLBACK")
            raise
        except InvalidEmojiFileError:
            self._db.execute("ROLLBACK")
            raise
        except Exception as e:
            self._db.execute("ROLLBACK")
            # Check if it's a UNIQUE constraint violation (might not have explicit EmojiNameExistsError)
            if "UNIQUE" in str(e) or "unique" in str(e).lower():
                raise EmojiNameExistsError(
                    f"Emoji with name '{name}' already exists in this server"
                )
            raise

        logger.debug(f"Custom emoji {name} created for server {server_id}")

        result = self.get_custom_emoji(emoji_id)
        assert result is not None
        return result

    def update_custom_emoji(
        self,
        user_id: SnowflakeID,
        emoji_id: SnowflakeID,
        name: Optional[str] = None,
    ) -> CustomEmoji:
        """
        Update a custom emoji's name.

        Args:
            user_id: ID of user updating
            emoji_id: ID of emoji
            name: New name (optional)

        Returns:
            Updated CustomEmoji

        Raises:
            CustomEmojiNotFoundError: Emoji not found
            PermissionDeniedError: No permission
            InvalidEmojiNameError: Invalid name
            EmojiNameExistsError: Name already taken
        """
        emoji = self.get_custom_emoji(emoji_id)
        if not emoji:
            raise CustomEmojiNotFoundError("Custom emoji not found")

        if self._servers:
            if not self._servers.has_permission(
                user_id, emoji.server_id, "server.manage"
            ):
                raise PermissionDeniedError(
                    "Missing permission to manage server", "server.manage"
                )

        if name is not None:
            name = self._validate_emoji_name(name)

            # Check uniqueness (excluding current emoji)
            existing = self._db.fetch_one(
                "SELECT 1 FROM react_custom_emoji WHERE server_id = ? AND name = ? AND id != ?",
                (emoji.server_id, name, emoji_id),
            )
            if existing:
                raise EmojiNameExistsError(
                    f"Emoji with name '{name}' already exists in this server"
                )

            self._db.execute(
                "UPDATE react_custom_emoji SET name = ? WHERE id = ?", (name, emoji_id)
            )

        logger.debug(f"Custom emoji {emoji_id} updated")

        result = self.get_custom_emoji(emoji_id)
        assert result is not None
        return result

    def delete_custom_emoji(self, user_id: SnowflakeID, emoji_id: SnowflakeID) -> bool:
        """
        Delete a custom emoji.

        Args:
            user_id: ID of user deleting
            emoji_id: ID of emoji

        Returns:
            True if deleted

        Raises:
            CustomEmojiNotFoundError: Emoji not found
            PermissionDeniedError: No permission
        """
        emoji = self.get_custom_emoji(emoji_id)
        if not emoji:
            raise CustomEmojiNotFoundError("Custom emoji not found")

        if self._servers:
            if not self._servers.has_permission(
                user_id, emoji.server_id, "server.manage"
            ):
                raise PermissionDeniedError(
                    "Missing permission to manage server", "server.manage"
                )

        self._db.execute(
            "DELETE FROM react_reactions WHERE custom_emoji_id = ?", (emoji_id,)
        )

        self._db.execute("DELETE FROM react_custom_emoji WHERE id = ?", (emoji_id,))

        logger.debug(f"Custom emoji {emoji_id} deleted")

        return True

    def get_custom_emoji(self, emoji_id: int) -> Optional[CustomEmoji]:
        """Get a custom emoji by ID."""
        row = self._db.fetch_one(
            "SELECT * FROM react_custom_emoji WHERE id = ?", (emoji_id,)
        )

        if not row:
            return None

        return self._row_to_custom_emoji(row)

    def get_server_custom_emojis(
        self, server_id: int, include_unavailable: bool = False
    ) -> List[CustomEmoji]:
        """Get all custom emojis for a server."""
        query = """
            SELECT e.*, u.username as uploader_username 
            FROM react_custom_emoji e
            LEFT JOIN auth_users u ON e.created_by = u.id
            WHERE e.server_id = ?
        """
        
        if not include_unavailable:
            query += " AND e.available = 1"
            
        query += " ORDER BY e.name"
        
        rows = self._db.fetch_all(query, (server_id,))

        return [self._row_to_custom_emoji(row) for row in rows]

    def get_emoji_counts(self, server_id: SnowflakeID) -> Dict[str, int]:
        """Get emoji counts for a server."""
        static_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM react_custom_emoji WHERE server_id = ? AND animated = 0",
            (server_id,),
        )
        animated_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM react_custom_emoji WHERE server_id = ? AND animated = 1",
            (server_id,),
        )
        return {
            "static": static_row["count"] if static_row else 0,
            "animated": animated_row["count"] if animated_row else 0,
            "max_static": self._config.get("max_emojis_per_server", 50),
            "max_animated": self._config.get("max_animated_emojis_per_server", 50),
        }

    def get_reactions_batch(
        self, user_id: int, message_ids: List[int]
    ) -> Dict[int, List[Dict[str, Any]]]:
        """
        Get reactions for multiple messages in a single batch query.

        This is optimized to avoid N+1 queries when loading message lists.

        Args:
            user_id: ID of user requesting (for 'me' field)
            message_ids: List of message IDs to get reactions for

        Returns:
            Dict mapping message_id to list of reaction dicts with emoji, count, me
        """
        if not message_ids:
            return {}

        logger.debug(f"Batch fetching reactions for {len(message_ids)} messages")

        # Get blocked users for filtering
        blocked_users = set()
        if self._relationships:
            blocked_ids = self._relationships.get_blocked_user_ids(user_id)
            blocked_users.update(blocked_ids)
            rows = self._db.fetch_all(
                "SELECT blocker_id FROM rel_blocked WHERE blocked_id = ?", (user_id,)
            )
            for row in rows:
                blocked_users.add(row["blocker_id"])

        # Build query with placeholders
        placeholders = ",".join("?" * len(message_ids))

        # Base query
        query = f"""SELECT r.message_id, r.emoji, r.is_custom, r.custom_emoji_id, e.url,
                           COUNT(*) as count,
                           MAX(CASE WHEN r.user_id = ? THEN 1 ELSE 0 END) as me
                    FROM react_reactions r
                    LEFT JOIN react_custom_emoji e ON r.custom_emoji_id = e.id
                    WHERE r.message_id IN ({placeholders})"""
        params = [user_id] + list(message_ids)

        # Filter out blocked users in the main query to avoid N+1
        if blocked_users:
            blocked_placeholders = ",".join("?" * len(blocked_users))
            query += f" AND r.user_id NOT IN ({blocked_placeholders})"
            params.extend(list(blocked_users))

        query += " GROUP BY r.message_id, r.emoji, r.is_custom, r.custom_emoji_id, e.url ORDER BY r.message_id, MIN(r.created_at)"

        # Single query to get all filtered reactions grouped by message and emoji
        rows = self._db.fetch_all(query, tuple(params))

        # Build result dict
        result: Dict[int, List[Dict[str, Any]]] = {mid: [] for mid in message_ids}

        for row in rows:
            msg_id = row["message_id"]
            count = row["count"]

            if count > 0:
                result[msg_id].append(
                    {
                        "emoji": row["emoji"],
                        "count": count,
                        "me": bool(row["me"]),
                        "is_custom": bool(row["is_custom"]),
                        "custom_emoji_id": row["custom_emoji_id"],
                        "url": row.get("url")
                    }
                )

        return result

    def _row_to_reaction(self, row) -> Reaction:
        """Convert database row to Reaction."""
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
        """Convert database row to CustomEmoji."""
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

    def get_reaction_count(self, message_id: SnowflakeID) -> int:
        """Get total count of reactions on a message."""
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM react_reactions WHERE message_id = ?",
            (message_id,),
        )
        return row["count"] if row else 0

    def get_reaction_count_by_emoji(self, message_id: SnowflakeID, emoji: str) -> int:
        """Get count of a specific emoji on a message."""
        is_custom, custom_id, normalized = self._validate_emoji(emoji)

        if is_custom:
            row = self._db.fetch_one(
                "SELECT COUNT(*) as count FROM react_reactions WHERE message_id = ? AND custom_emoji_id = ?",
                (message_id, custom_id),
            )
        else:
            row = self._db.fetch_one(
                "SELECT COUNT(*) as count FROM react_reactions WHERE message_id = ? AND emoji = ? AND is_custom = 0",
                (message_id, normalized),
            )
        return row["count"] if row else 0

    def user_reacted_to_messages(
        self, user_id: int, message_ids: List[int]
    ) -> List[int]:
        """Check which of the given messages a user has reacted to."""
        if not message_ids:
            return []

        placeholders = ",".join("?" * len(message_ids))
        rows = self._db.fetch_all(
            f"SELECT DISTINCT message_id FROM react_reactions WHERE user_id = ? AND message_id IN ({placeholders})",
            (user_id, *message_ids),
        )
        return [row["message_id"] for row in rows]

    def get_users_who_reacted(
        self, message_id: SnowflakeID, emoji: str
    ) -> List[SnowflakeID]:
        """Legacy helper for tests."""
        users = self.get_reaction_users(user_id=1, message_id=message_id, emoji=emoji)
        return [u.user_id for u in users]

    def get_reactions_bulk(
        self, message_ids: List[int]
    ) -> Dict[int, List[Dict[str, Any]]]:
        """Legacy helper for tests."""
        return self.get_reactions_batch(user_id=1, message_ids=message_ids)
