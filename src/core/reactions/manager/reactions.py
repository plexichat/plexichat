from typing import Optional, List, Dict, Any
import utils.logger as logger
from src.core.base import SnowflakeID
from ..models import (
    Reaction,
    ReactionCount,
    ReactionUser,
    MessageReactions,
)
from ..exceptions import (
    MessageNotFoundError,
    ReactionNotFoundError,
    ReactionExistsError,
    CustomEmojiNotFoundError,
    ReactionLimitError,
    PermissionDeniedError,
)


from .protocol import ReactionProtocol


class ReactionOpsMixin(ReactionProtocol):
    def add_reaction(
        self, user_id: SnowflakeID, message_id: SnowflakeID, emoji: str
    ) -> Reaction:
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
            if custom_emoji_id is None:
                raise CustomEmojiNotFoundError("Custom emoji not found in this server")
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
        if channel:
            srv_row = self._db.fetch_one(
                "SELECT max_reactions_per_message FROM srv_servers WHERE id = ?",
                (channel["server_id"],),
            )
            if srv_row and srv_row["max_reactions_per_message"]:
                max_reactions = srv_row["max_reactions_per_message"]

        max_user_reactions = self._config.get("max_unique_reactions_per_user", 50)

        try:
            self._db.begin_transaction()

            existing = self._db.fetch_one(
                "SELECT 1 FROM react_reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
                (message_id, user_id, normalized_emoji),
            )
            if existing:
                self._db.rollback()
                raise ReactionExistsError("You have already reacted with this emoji")

            unique_count = self._get_unique_emoji_count(message_id)
            user_has_this_emoji = self._db.fetch_one(
                "SELECT 1 FROM react_reactions WHERE message_id = ? AND emoji = ?",
                (message_id, normalized_emoji),
            )

            if not user_has_this_emoji and unique_count >= max_reactions:
                self._db.rollback()
                raise ReactionLimitError(
                    f"Message has reached maximum of {max_reactions} unique reactions",
                    max_reactions,
                    unique_count,
                )

            user_unique_count = self._db.fetch_one(
                "SELECT count(DISTINCT emoji) as count FROM react_reactions WHERE message_id = ? AND user_id = ?",
                (message_id, user_id),
            )["count"]

            if not user_has_this_emoji and user_unique_count >= max_user_reactions:
                self._db.rollback()
                raise ReactionLimitError(
                    f"User has reached maximum of {max_user_reactions} unique reactions per message",
                    max_user_reactions,
                    user_unique_count,
                )

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

            self._db.commit()

        except ReactionLimitError:
            self._db.rollback()
            raise
        except ReactionExistsError:
            self._db.rollback()
            raise
        except Exception:
            self._db.rollback()
            raise

        logger.debug(
            f"Reaction {reaction_id} added to message {message_id} by user {user_id}"
        )

        result = self.get_reaction(reaction_id)
        assert result is not None
        return result

    def remove_reaction(
        self, user_id: SnowflakeID, message_id: SnowflakeID, emoji: str
    ) -> bool:
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
        row = self._db.fetch_one(
            "SELECT * FROM react_reactions WHERE id = ?", (reaction_id,)
        )

        if not row:
            return None

        return self._row_to_reaction(row)

    def get_reactions(
        self, user_id: SnowflakeID, message_id: SnowflakeID
    ) -> MessageReactions:
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

    def get_reactions_batch(
        self, user_id: int, message_ids: List[int]
    ) -> Dict[int, List[Dict[str, Any]]]:
        if not message_ids:
            return {}

        logger.debug(f"Batch fetching reactions for {len(message_ids)} messages")

        blocked_users = set()
        if self._relationships:
            blocked_ids = self._relationships.get_blocked_user_ids(user_id)
            blocked_users.update(blocked_ids)
            rows = self._db.fetch_all(
                "SELECT blocker_id FROM rel_blocked WHERE blocked_id = ?", (user_id,)
            )
            for row in rows:
                blocked_users.add(row["blocker_id"])

        placeholders = ",".join("?" * len(message_ids))

        query = f"""SELECT r.message_id, r.emoji, r.is_custom, r.custom_emoji_id, e.url,
                           COUNT(*) as count,
                           MAX(CASE WHEN r.user_id = ? THEN 1 ELSE 0 END) as me
                    FROM react_reactions r
                    LEFT JOIN react_custom_emoji e ON r.custom_emoji_id = e.id
                    WHERE r.message_id IN ({placeholders})"""
        params = [user_id] + list(message_ids)

        if blocked_users:
            blocked_placeholders = ",".join("?" * len(blocked_users))
            query += f" AND r.user_id NOT IN ({blocked_placeholders})"
            params.extend(list(blocked_users))

        query += " GROUP BY r.message_id, r.emoji, r.is_custom, r.custom_emoji_id, e.url ORDER BY r.message_id, MIN(r.created_at)"

        rows = self._db.fetch_all(query, tuple(params))

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
                        "url": row.get("url"),
                    }
                )

        return result

    def get_reaction_count(self, message_id: SnowflakeID) -> int:
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM react_reactions WHERE message_id = ?",
            (message_id,),
        )
        return row["count"] if row else 0

    def get_reaction_count_by_emoji(self, message_id: SnowflakeID, emoji: str) -> int:
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
        users = self.get_reaction_users(user_id=1, message_id=message_id, emoji=emoji)
        return [u.user_id for u in users]

    def get_reactions_bulk(
        self, message_ids: List[int]
    ) -> Dict[int, List[Dict[str, Any]]]:
        return self.get_reactions_batch(user_id=1, message_ids=message_ids)
