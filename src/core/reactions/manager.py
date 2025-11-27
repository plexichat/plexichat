"""
Reaction manager - Core business logic for reaction operations.

Handles adding, removing, and querying reactions with proper
validation, permission checks, and database interactions.
"""

import re
import time
from typing import Optional, List, Dict, Any

import utils.config as config
import utils.logger as logger
from src.utils.encryption import generate_snowflake_id

from .models import (
    Reaction,
    ReactionCount,
    ReactionUser,
    MessageReactions,
    CustomEmoji,
)
from .exceptions import (
    MessageNotFoundError,
    ReactionNotFoundError,
    ReactionExistsError,
    InvalidEmojiError,
    CustomEmojiNotFoundError,
    ReactionLimitError,
    PermissionDeniedError,
    UserBlockedError,
)
from .schema import create_tables


CUSTOM_EMOJI_PATTERN = re.compile(r"^<a?:([a-zA-Z0-9_]+):(\d+)>$")


class ReactionManager:
    """Core reaction manager handling all operations."""

    def __init__(self, db, messaging_module=None, servers_module=None, relationships_module=None):
        """
        Initialize the reaction manager.

        Args:
            db: Database instance (must be connected)
            messaging_module: Messaging module for message access
            servers_module: Servers module for permission checks
            relationships_module: Relationships module for block filtering
        """
        self._db = db
        self._messaging = messaging_module
        self._servers = servers_module
        self._relationships = relationships_module
        self._config = self._load_config()

        create_tables(db)

        logger.info("Reaction module initialized")

    def _load_config(self) -> Dict[str, Any]:
        """Load reaction configuration."""
        defaults = {
            "max_reactions_per_message": 20,
            "max_users_per_reaction_page": 100,
        }

        reactions_config = config.get("reactions", {})
        return {**defaults, **reactions_config}

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

    def _get_conversation(self, conversation_id: int) -> Optional[Dict]:
        """Get conversation from database."""
        return self._db.fetch_one(
            "SELECT * FROM msg_conversations WHERE id = ? AND deleted = 0",
            (conversation_id,)
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

    def _check_server_permission(self, user_id: int, server_id: int, channel_id: int = None) -> bool:
        """Check if user has add_reactions permission in server."""
        if not self._servers:
            return True
        return self._servers.has_permission(user_id, server_id, "messages.add_reactions", channel_id)

    def _is_blocked_by_either(self, user_id: int, other_id: int) -> bool:
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
            emoji_name = custom_match.group(1)
            emoji_id = int(custom_match.group(2))
            return (True, emoji_id, emoji)

        if len(emoji) > 32:
            raise InvalidEmojiError("Emoji is too long")

        return (False, None, emoji)

    def _validate_custom_emoji_for_server(self, custom_emoji_id: int, server_id: int) -> bool:
        """Validate that custom emoji exists in the server."""
        row = self._db.fetch_one(
            "SELECT 1 FROM react_custom_emoji WHERE id = ? AND server_id = ?",
            (custom_emoji_id, server_id)
        )
        return row is not None

    def _get_unique_emoji_count(self, message_id: int) -> int:
        """Get count of unique emoji on a message."""
        row = self._db.fetch_one(
            "SELECT COUNT(DISTINCT emoji) as count FROM react_reactions WHERE message_id = ?",
            (message_id,)
        )
        return row["count"] if row else 0

    def add_reaction(
        self,
        user_id: int,
        message_id: int,
        emoji: str
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
            if not self._check_server_permission(user_id, channel["server_id"], channel["id"]):
                raise PermissionDeniedError(
                    "Missing permission to add reactions",
                    "messages.add_reactions"
                )

        is_custom, custom_emoji_id, normalized_emoji = self._validate_emoji(emoji)

        if is_custom and channel:
            if not self._validate_custom_emoji_for_server(custom_emoji_id, channel["server_id"]):
                raise CustomEmojiNotFoundError("Custom emoji not found in this server")

        existing = self._db.fetch_one(
            "SELECT 1 FROM react_reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
            (message_id, user_id, normalized_emoji)
        )
        if existing:
            raise ReactionExistsError("You have already reacted with this emoji")

        max_reactions = self._config.get("max_reactions_per_message", 20)
        unique_count = self._get_unique_emoji_count(message_id)

        user_has_this_emoji = self._db.fetch_one(
            "SELECT 1 FROM react_reactions WHERE message_id = ? AND emoji = ?",
            (message_id, normalized_emoji)
        )

        if not user_has_this_emoji and unique_count >= max_reactions:
            raise ReactionLimitError(
                f"Message has reached maximum of {max_reactions} unique reactions",
                max_reactions,
                unique_count
            )

        now = self._get_timestamp()
        reaction_id = self._generate_id()

        self._db.execute(
            """INSERT INTO react_reactions 
               (id, message_id, user_id, emoji, is_custom, custom_emoji_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (reaction_id, message_id, user_id, normalized_emoji, 
             1 if is_custom else 0, custom_emoji_id, now)
        )

        logger.debug(f"Reaction {reaction_id} added to message {message_id} by user {user_id}")

        return self.get_reaction(reaction_id)

    def remove_reaction(
        self,
        user_id: int,
        message_id: int,
        emoji: str
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

        existing = self._db.fetch_one(
            "SELECT id FROM react_reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
            (message_id, user_id, normalized_emoji)
        )
        if not existing:
            raise ReactionNotFoundError("You have not reacted with this emoji")

        self._db.execute(
            "DELETE FROM react_reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
            (message_id, user_id, normalized_emoji)
        )

        logger.debug(f"Reaction removed from message {message_id} by user {user_id}")

        return True

    def remove_all_reactions(
        self,
        user_id: int,
        message_id: int
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
                    "Missing permission to manage messages",
                    "messages.manage"
                )
        else:
            conv = self._get_conversation(msg["conversation_id"])
            if conv and conv["owner_id"] != user_id:
                participant = self._db.fetch_one(
                    "SELECT role FROM msg_participants WHERE conversation_id = ? AND user_id = ?",
                    (msg["conversation_id"], user_id)
                )
                if not participant or participant["role"] not in ("owner", "admin"):
                    raise PermissionDeniedError("Only conversation owner or admin can remove all reactions")

        count_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM react_reactions WHERE message_id = ?",
            (message_id,)
        )
        count = count_row["count"] if count_row else 0

        self._db.execute(
            "DELETE FROM react_reactions WHERE message_id = ?",
            (message_id,)
        )

        logger.debug(f"All reactions ({count}) removed from message {message_id} by user {user_id}")

        return count

    def remove_all_reactions_for_emoji(
        self,
        user_id: int,
        message_id: int,
        emoji: str
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
                    "Missing permission to manage messages",
                    "messages.manage"
                )
        else:
            conv = self._get_conversation(msg["conversation_id"])
            if conv and conv["owner_id"] != user_id:
                participant = self._db.fetch_one(
                    "SELECT role FROM msg_participants WHERE conversation_id = ? AND user_id = ?",
                    (msg["conversation_id"], user_id)
                )
                if not participant or participant["role"] not in ("owner", "admin"):
                    raise PermissionDeniedError("Only conversation owner or admin can remove reactions")

        is_custom, custom_emoji_id, normalized_emoji = self._validate_emoji(emoji)

        count_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM react_reactions WHERE message_id = ? AND emoji = ?",
            (message_id, normalized_emoji)
        )
        count = count_row["count"] if count_row else 0

        self._db.execute(
            "DELETE FROM react_reactions WHERE message_id = ? AND emoji = ?",
            (message_id, normalized_emoji)
        )

        logger.debug(f"All reactions for emoji {emoji} ({count}) removed from message {message_id}")

        return count

    def get_reaction(self, reaction_id: int) -> Optional[Reaction]:
        """Get a reaction by ID."""
        row = self._db.fetch_one(
            "SELECT * FROM react_reactions WHERE id = ?",
            (reaction_id,)
        )

        if not row:
            return None

        return self._row_to_reaction(row)

    def get_reactions(
        self,
        user_id: int,
        message_id: int
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
                "SELECT blocker_id FROM rel_blocked WHERE blocked_id = ?",
                (user_id,)
            )
            for row in rows:
                blocked_users.add(row["blocker_id"])

        rows = self._db.fetch_all(
            """SELECT emoji, is_custom, custom_emoji_id, COUNT(*) as count,
                      MAX(CASE WHEN user_id = ? THEN 1 ELSE 0 END) as me
               FROM react_reactions 
               WHERE message_id = ?
               GROUP BY emoji, is_custom, custom_emoji_id
               ORDER BY MIN(created_at)""",
            (user_id, message_id)
        )

        reactions = []
        total = 0

        for row in rows:
            if blocked_users:
                actual_count = self._db.fetch_one(
                    """SELECT COUNT(*) as count FROM react_reactions 
                       WHERE message_id = ? AND emoji = ? AND user_id NOT IN ({})""".format(
                        ",".join("?" * len(blocked_users))
                    ),
                    (message_id, row["emoji"]) + tuple(blocked_users)
                )
                count = actual_count["count"] if actual_count else 0
            else:
                count = row["count"]

            if count > 0:
                reactions.append(ReactionCount(
                    message_id=message_id,
                    emoji=row["emoji"],
                    count=count,
                    is_custom=bool(row["is_custom"]),
                    custom_emoji_id=row["custom_emoji_id"],
                    me=bool(row["me"])
                ))
                total += count

        return MessageReactions(
            message_id=message_id,
            reactions=reactions,
            total_count=total
        )

    def get_reaction_users(
        self,
        user_id: int,
        message_id: int,
        emoji: str,
        limit: int = 100,
        after_user_id: Optional[int] = None
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
                "SELECT blocker_id FROM rel_blocked WHERE blocked_id = ?",
                (user_id,)
            )
            for row in rows:
                blocked_users.add(row["blocker_id"])

        query = """SELECT user_id, created_at FROM react_reactions 
                   WHERE message_id = ? AND emoji = ?"""
        params = [message_id, normalized_emoji]

        if blocked_users:
            query += " AND user_id NOT IN ({})".format(",".join("?" * len(blocked_users)))
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
        self,
        user_id: int,
        message_id: int
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
            (message_id, user_id)
        )

        return [self._row_to_reaction(row) for row in rows]

    def create_custom_emoji(
        self,
        user_id: int,
        server_id: int,
        name: str,
        animated: bool = False
    ) -> CustomEmoji:
        """
        Create a custom emoji for a server.
        
        Args:
            user_id: ID of user creating emoji
            server_id: ID of server
            name: Emoji name (alphanumeric and underscores)
            animated: Whether emoji is animated
            
        Returns:
            Created CustomEmoji
            
        Raises:
            PermissionDeniedError: No permission
            InvalidEmojiError: Invalid name
        """
        if self._servers:
            if not self._servers.has_permission(user_id, server_id, "server.manage"):
                raise PermissionDeniedError(
                    "Missing permission to manage server",
                    "server.manage"
                )

        if not name or not re.match(r"^[a-zA-Z0-9_]{2,32}$", name):
            raise InvalidEmojiError("Emoji name must be 2-32 alphanumeric characters or underscores")

        existing = self._db.fetch_one(
            "SELECT 1 FROM react_custom_emoji WHERE server_id = ? AND name = ?",
            (server_id, name)
        )
        if existing:
            raise InvalidEmojiError("Custom emoji with this name already exists")

        now = self._get_timestamp()
        emoji_id = self._generate_id()

        self._db.execute(
            """INSERT INTO react_custom_emoji (id, server_id, name, animated, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (emoji_id, server_id, name, 1 if animated else 0, now)
        )

        logger.debug(f"Custom emoji {name} created for server {server_id}")

        return self.get_custom_emoji(emoji_id)

    def delete_custom_emoji(
        self,
        user_id: int,
        emoji_id: int
    ) -> bool:
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
            if not self._servers.has_permission(user_id, emoji.server_id, "server.manage"):
                raise PermissionDeniedError(
                    "Missing permission to manage server",
                    "server.manage"
                )

        self._db.execute(
            "DELETE FROM react_reactions WHERE custom_emoji_id = ?",
            (emoji_id,)
        )

        self._db.execute(
            "DELETE FROM react_custom_emoji WHERE id = ?",
            (emoji_id,)
        )

        logger.debug(f"Custom emoji {emoji_id} deleted")

        return True

    def get_custom_emoji(self, emoji_id: int) -> Optional[CustomEmoji]:
        """Get a custom emoji by ID."""
        row = self._db.fetch_one(
            "SELECT * FROM react_custom_emoji WHERE id = ?",
            (emoji_id,)
        )

        if not row:
            return None

        return self._row_to_custom_emoji(row)

    def get_server_custom_emojis(self, server_id: int) -> List[CustomEmoji]:
        """Get all custom emojis for a server."""
        rows = self._db.fetch_all(
            "SELECT * FROM react_custom_emoji WHERE server_id = ? ORDER BY name",
            (server_id,)
        )

        return [self._row_to_custom_emoji(row) for row in rows]

    def _row_to_reaction(self, row) -> Reaction:
        """Convert database row to Reaction."""
        return Reaction(
            id=row["id"],
            message_id=row["message_id"],
            user_id=row["user_id"],
            emoji=row["emoji"],
            is_custom=bool(row["is_custom"]),
            custom_emoji_id=row["custom_emoji_id"],
            created_at=row["created_at"]
        )

    def _row_to_custom_emoji(self, row) -> CustomEmoji:
        """Convert database row to CustomEmoji."""
        return CustomEmoji(
            id=row["id"],
            server_id=row["server_id"],
            name=row["name"],
            animated=bool(row["animated"]),
            created_at=row["created_at"]
        )
