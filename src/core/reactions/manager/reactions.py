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
    UserBlockedError,
    InvalidEmojiError,
)


from .bounds import validate_bulk_message_ids
from .cache import reaction_read_key, _MISS
from .protocol import ReactionProtocol


# One statement re-checks both reaction quotas at the instant of the insert.
# The old flow read the message-wide and per-user distinct counts in a plain
# SELECT and only then issued a bare INSERT. Under PostgreSQL READ COMMITTED
# two concurrent adds can both observe below-cap counts and both insert,
# pushing the message (or the user) past the configured quota -- the same
# check-then-insert TOCTOU the custom-emoji quota was hardened against in
# emojis.py. The guard subqueries see table state *before* the new row exists,
# so exactly the callers that still fit get a row and rowcount == 0 is an
# authoritative "limit reached / duplicate" signal. add_reaction() additionally
# takes an advisory xact lock on Postgres to serialize concurrent adds to the
# same message; on SQLite the single statement is serialized by the write lock.
# A subquery table alias keeps these guards distinct from the early grouped
# quota probe's text (which callers/tests rely on for the fast path).
_ATOMIC_REACTION_INSERT_SQL = """
INSERT INTO react_reactions
    (id, message_id, user_id, emoji, is_custom, custom_emoji_id, created_at)
SELECT ?, ?, ?, ?, ?, ?, ?
WHERE NOT EXISTS (
    SELECT 1 FROM react_reactions
    WHERE message_id = ? AND user_id = ? AND emoji = ?
)
AND (
    EXISTS (SELECT 1 FROM react_reactions rr WHERE rr.message_id = ? AND rr.emoji = ?)
    OR (SELECT COUNT(DISTINCT rr.emoji) FROM react_reactions rr WHERE rr.message_id = ?) < ?
)
AND (SELECT COUNT(DISTINCT CASE WHEN rr.user_id = ? THEN rr.emoji END)
     FROM react_reactions rr WHERE rr.message_id = ?) < ?
"""


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

        if self._relationships and self._is_blocked_by_either(
            user_id, msg["author_id"]
        ):
            raise UserBlockedError("Cannot react to this message")

        is_custom, custom_emoji_id, normalized_emoji = self._validate_emoji(emoji)

        if is_custom:
            if custom_emoji_id is None:
                raise CustomEmojiNotFoundError("Custom emoji not found")
            # Resolve the owning server from the database, never from the
            # attacker-controlled token. Channel messages already scope the
            # emoji to the channel's server below, but a DM has no channel and
            # previously skipped every server check: any caller who knew an
            # emoji id could use an emoji owned by a server they are not in
            # (including private servers). Gate on real membership of the
            # owning server in every context and fail closed when it cannot
            # be verified.
            owner_row = self._db.fetch_one(
                "SELECT server_id FROM react_custom_emoji "
                "WHERE id = ? AND available = 1",
                (custom_emoji_id,),
            )
            if not owner_row:
                raise CustomEmojiNotFoundError("Custom emoji not found")
            owner_server_id = owner_row["server_id"]
            if owner_server_id:
                servers_mod = self._servers
                if not servers_mod:
                    logger.warning(
                        "Servers module unavailable; denying custom emoji %s "
                        "whose owning server cannot be verified",
                        custom_emoji_id,
                    )
                    raise CustomEmojiNotFoundError("Custom emoji not found")
                try:
                    member = servers_mod.get_member(owner_server_id, user_id)
                except Exception as exc:
                    logger.error(
                        "Failed to verify membership for custom emoji %s in "
                        "server %s: %s",
                        custom_emoji_id,
                        owner_server_id,
                        exc,
                    )
                    raise CustomEmojiNotFoundError("Custom emoji not found") from exc
                if not member:
                    raise CustomEmojiNotFoundError("Custom emoji not found")
            if channel:
                if not self._validate_custom_emoji_for_server(
                    custom_emoji_id, channel["server_id"]
                ):
                    raise CustomEmojiNotFoundError(
                        "Custom emoji not found in this server"
                    )

        # The duplicate-reaction check used to run here *and* again inside the
        # transaction below.  The in-transaction probe is the authoritative one
        # (it is race-safe, and the UNIQUE(message_id, user_id, emoji) constraint
        # is the final backstop), so this pre-transaction copy was a redundant
        # extra query on every single add.  Dropping it saves one round trip on
        # the hot path without changing the error mapping.

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

            if getattr(self._db, "type", "") == "postgres":
                # PostgreSQL READ COMMITTED would let two concurrent adds both
                # pass the quota probe below. Serialize per message with an
                # advisory xact lock; namespace 102 stays clear of the emoji
                # (100/101) and invite (1/2) lock namespaces, and masking to 30
                # bits keeps the key in signed int4 range.
                self._db.execute(
                    "SELECT pg_advisory_xact_lock(?, ?)",
                    (102, int(message_id) & 0x3FFFFFFF),
                )

            existing = self._db.fetch_one(
                "SELECT 1 FROM react_reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
                (message_id, user_id, normalized_emoji),
            )
            if existing:
                self._db.rollback()
                raise ReactionExistsError("You have already reacted with this emoji")

            # All three quota gates below read the same ``message_id`` slice of
            # react_reactions. Fold them into one conditional-aggregate pass
            # instead of scanning the rows three separate times (message-wide
            # distinct count, "is this emoji already on the message", and this
            # user's distinct count).
            quota = self._db.fetch_one(
                """SELECT COUNT(DISTINCT emoji) AS unique_count,
                          MAX(CASE WHEN emoji = ? THEN 1 ELSE 0 END) AS emoji_on_message,
                          COUNT(DISTINCT CASE WHEN user_id = ? THEN emoji END) AS user_unique_count
                   FROM react_reactions WHERE message_id = ?""",
                (normalized_emoji, user_id, message_id),
            )
            unique_count = quota["unique_count"] if quota else 0
            # NOTE: this is a MESSAGE-wide flag, not a per-user one -- it
            # answers "is this emoji already present on the message at all?".
            # It is the right gate for the message-level cap below (only a
            # genuinely new distinct emoji consumes a message slot), but it
            # must never gate the per-user cap.
            emoji_on_message = bool(quota["emoji_on_message"]) if quota else False
            user_unique_count = quota["user_unique_count"] if quota else 0

            if not emoji_on_message and unique_count >= max_reactions:
                self._db.rollback()
                raise ReactionLimitError(
                    f"Message has reached maximum of {max_reactions} unique reactions",
                    max_reactions,
                    unique_count,
                )

            # The per-user cap counts THIS user's distinct emoji, so it must be
            # evaluated unconditionally. It previously reused the
            # message-wide ``emoji_on_message`` flag as a short-circuit, which
            # silently disabled the cap for every emoji some other user had
            # already reacted with -- a user at their limit could keep adding
            # distinct emoji as long as each one already existed on the
            # message. The duplicate check above has already rejected the only
            # case where this insert would not grow the user's distinct count.
            if user_unique_count >= max_user_reactions:
                self._db.rollback()
                raise ReactionLimitError(
                    f"User has reached maximum of {max_user_reactions} unique reactions per message",
                    max_user_reactions,
                    user_unique_count,
                )

            now = self._get_timestamp()
            reaction_id = self._generate_id()

            # Authoritative, race-safe insert: the quota guards above are only
            # advisory feedback (a concurrent add can commit between them and
            # this statement). This single statement re-checks the duplicate and
            # both quotas against live table state at insert time. rowcount == 0
            # means the guard refused the row — a racer either created this
            # exact reaction first or consumed the last quota slot — so fail
            # closed and map the refusal to the usual error.
            cursor = self._db.execute(
                _ATOMIC_REACTION_INSERT_SQL,
                (
                    reaction_id,
                    message_id,
                    user_id,
                    normalized_emoji,
                    1 if is_custom else 0,
                    custom_emoji_id,
                    now,
                    message_id,
                    user_id,
                    normalized_emoji,
                    message_id,
                    normalized_emoji,
                    message_id,
                    max_reactions,
                    user_id,
                    message_id,
                    max_user_reactions,
                ),
            )

            if getattr(cursor, "rowcount", 0) == 0:
                duplicate = self._db.fetch_one(
                    "SELECT id FROM react_reactions "
                    "WHERE message_id = ? AND user_id = ? AND emoji = ? LIMIT 1",
                    (message_id, user_id, normalized_emoji),
                )
                if duplicate:
                    raise ReactionExistsError("You have already reacted with this emoji")
                raise ReactionLimitError(
                    "Reaction quota reached while adding reaction",
                    max_reactions,
                    0,
                )

            self._db.commit()
            # A committed write invalidates every viewer's cached aggregation:
            # counts and the per-viewer ``me`` flag for this message changed.
            self._invalidate_reaction_reads()

        except ReactionLimitError:
            self._db.rollback()
            raise
        except ReactionExistsError:
            self._db.rollback()
            raise
        except Exception as e:
            self._db.rollback()
            if self._is_unique_violation(e):
                raise ReactionExistsError("You have already reacted with this emoji")
            raise

        logger.debug(
            f"Reaction {reaction_id} added to message {message_id} by user {user_id}"
        )

        # Build the result from the values just written instead of re-reading
        # the row with ``get_reaction``.  Every field below is exactly what the
        # INSERT above persisted (``is_custom`` is stored as 0/1 and restored
        # to bool), so callers receive an identical ``Reaction`` without the
        # extra SELECT on this hot path.
        return Reaction(
            id=reaction_id,
            message_id=message_id,
            user_id=user_id,
            emoji=normalized_emoji,
            is_custom=bool(is_custom),
            custom_emoji_id=custom_emoji_id,
            created_at=now,
        )

    def remove_reaction(
        self, user_id: SnowflakeID, message_id: SnowflakeID, emoji: str
    ) -> bool:
        msg = self._get_message(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")

        if not self._is_participant(msg["conversation_id"], user_id):
            raise MessageNotFoundError("Message not found")

        if self._relationships and self._is_blocked_by_either(
            user_id, msg["author_id"]
        ):
            raise UserBlockedError("Cannot react to this message")

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

        cursor = self._db.execute(
            "DELETE FROM react_reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
            (message_id, user_id, normalized_emoji),
        )

        if getattr(cursor, "rowcount", 1) == 0:
            raise ReactionNotFoundError("Reaction not found")

        logger.debug(f"Reaction removed from message {message_id} by user {user_id}")

        self._invalidate_reaction_reads()
        return True

    def remove_all_reactions(
        self, user_id: SnowflakeID, message_id: SnowflakeID
    ) -> int:
        msg = self._get_message(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")

        if not self._is_participant(msg["conversation_id"], user_id):
            raise MessageNotFoundError("Message not found")

        channel = self._get_channel_for_conversation(msg["conversation_id"])
        if channel:
            if not self._check_server_permission(
                user_id,
                channel["server_id"],
                channel["id"],
                permission="messages.manage",
            ):
                raise PermissionDeniedError(
                    "Missing permission to manage messages", "messages.manage"
                )
        else:
            # Fail closed: a soft-deleted (or missing) conversation must deny
            # the wipe instead of skipping the permission block entirely.
            conv = self._get_conversation(msg["conversation_id"])
            if not conv:
                raise PermissionDeniedError(
                    "Cannot verify permissions to remove all reactions"
                )
            if conv["owner_id"] != user_id:
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

        self._invalidate_reaction_reads()
        return count

    def remove_all_reactions_for_emoji(
        self, user_id: int, message_id: int, emoji: str
    ) -> int:
        msg = self._get_message(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")

        if not self._is_participant(msg["conversation_id"], user_id):
            raise MessageNotFoundError("Message not found")

        channel = self._get_channel_for_conversation(msg["conversation_id"])
        if channel:
            if not self._check_server_permission(
                user_id,
                channel["server_id"],
                channel["id"],
                permission="messages.manage",
            ):
                raise PermissionDeniedError(
                    "Missing permission to manage messages", "messages.manage"
                )
        else:
            # Fail closed: a soft-deleted (or missing) conversation must deny
            # the wipe instead of skipping the permission block entirely.
            conv = self._get_conversation(msg["conversation_id"])
            if not conv:
                raise PermissionDeniedError(
                    "Cannot verify permissions to remove reactions"
                )
            if conv["owner_id"] != user_id:
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

        self._invalidate_reaction_reads()
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
        # NOTE: this single-message aggregate is deliberately NOT memoized here.
        # The HTTP layer already caches its serialized body with a per-credential
        # key (``authenticated_cache_key``), and callers/tests rely on each call
        # re-running the block-filter query (e.g. the deterministic-params
        # regression).  The L0 cache below targets the *other* hot read paths
        # (batch hydration, counts, reactor lists) that have no response cache.
        msg = self._get_message(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")

        if not self._is_participant(msg["conversation_id"], user_id):
            raise MessageNotFoundError("Message not found")

        query = """SELECT r.emoji, r.is_custom, r.custom_emoji_id, e.url, COUNT(*) as count,
                          MAX(CASE WHEN r.user_id = ? THEN 1 ELSE 0 END) as me
                   FROM react_reactions r
                   LEFT JOIN react_custom_emoji e ON r.custom_emoji_id = e.id
                   WHERE r.message_id = ?"""

        params = [user_id, message_id]

        clause, blocked_params = self._blocked_users_clause("r.user_id", user_id)
        query += clause
        params.extend(blocked_params)

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
        credential: Optional[str] = None,
    ) -> List[ReactionUser]:
        # LIVE GATE: the message + participant checks stay OUTSIDE the cache so
        # a viewer who loses access cannot read a warm reactor list.
        msg = self._get_message(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")

        if not self._is_participant(msg["conversation_id"], user_id):
            raise MessageNotFoundError("Message not found")

        # Legacy/stored emoji rows may no longer pass the (now stricter)
        # validator; treat them as "no users" instead of erroring reads.
        try:
            is_custom, custom_emoji_id, normalized_emoji = self._validate_emoji(emoji)
        except InvalidEmojiError:
            return []

        max_per_page = self._config.get("max_users_per_reaction_page", 100)
        # Clamp from below too: SQLite treats LIMIT -1 as *unlimited*, so a
        # negative limit would bypass the page cap and dump every reactor.
        limit = max(0, min(limit, max_per_page))

        def _load_reaction_users() -> List[ReactionUser]:
            query = """SELECT user_id, created_at FROM react_reactions
                       WHERE message_id = ? AND emoji = ?"""
            params = [message_id, normalized_emoji]

            clause, blocked_params = self._blocked_users_clause("user_id", user_id)
            query += clause
            params.extend(blocked_params)

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

        key = reaction_read_key(
            "reaction_users",
            user_id,
            message_id,
            normalized_emoji,
            limit,
            after_user_id or 0,
            credential=credential,
        )
        return self._cached_reaction_read(key, _load_reaction_users)

    def get_user_reactions(
        self,
        user_id: SnowflakeID,
        message_id: SnowflakeID,
        credential: Optional[str] = None,
    ) -> List[Reaction]:
        # LIVE GATE before the cache lookup (see get_reaction_users).
        msg = self._get_message(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")

        if not self._is_participant(msg["conversation_id"], user_id):
            raise MessageNotFoundError("Message not found")

        def _load_user_reactions() -> List[Reaction]:
            rows = self._db.fetch_all(
                "SELECT * FROM react_reactions WHERE message_id = ? AND user_id = ? ORDER BY created_at",
                (message_id, user_id),
            )
            return [self._row_to_reaction(row) for row in rows]

        key = reaction_read_key(
            "user_reactions", user_id, message_id, credential=credential
        )
        return self._cached_reaction_read(key, _load_user_reactions)

    def _accessible_reaction_message_ids(self, user_id: int, message_ids: List[int]):
        """Live authorization gate for a batch reaction read.

        One bounded query resolves which of the requested messages this viewer
        may still read (the same participant/server-member rule the aggregation
        query uses).  It runs on *every* batch call, including cache hits, so a
        revoked membership hides its messages even while a warm aggregation
        body still exists.  Only the expensive aggregation is cached.
        """
        placeholders = ",".join("?" * len(message_ids))
        query = f"""SELECT m.id FROM msg_messages m
                    WHERE m.id IN ({placeholders})
                      AND (
                        m.conversation_id IN (
                            SELECT p.conversation_id FROM msg_participants p
                            WHERE p.user_id = ?
                        )
                        OR m.conversation_id IN (
                            SELECT sc.conversation_id FROM srv_channels sc
                            JOIN srv_members sm ON sc.server_id = sm.server_id
                            WHERE sm.user_id = ?
                        )
                      )"""
        rows = self._db.fetch_all(query, (*message_ids, user_id, user_id))
        return {row["id"] for row in rows}

    def get_reactions_batch(
        self,
        user_id: int,
        message_ids: List[int],
        credential: Optional[str] = None,
    ) -> Dict[int, List[Dict[str, Any]]]:
        if not message_ids:
            return {}
        message_ids = validate_bulk_message_ids(message_ids)

        logger.debug(f"Batch fetching reactions for {len(message_ids)} messages")

        def _load_batch() -> Dict[int, List[Dict[str, Any]]]:
            placeholders = ",".join("?" * len(message_ids))

            query = f"""SELECT r.message_id, r.emoji, r.is_custom, r.custom_emoji_id, e.url,
                               COUNT(*) as count,
                               MAX(CASE WHEN r.user_id = ? THEN 1 ELSE 0 END) as me
                        FROM react_reactions r
                        LEFT JOIN react_custom_emoji e ON r.custom_emoji_id = e.id
                        WHERE r.message_id IN ({placeholders})
                           AND (
                             r.message_id IN (
                                 SELECT m.id FROM msg_messages m
                                 JOIN msg_participants p ON m.conversation_id = p.conversation_id
                                 WHERE p.user_id = ?
                             )
                             OR r.message_id IN (
                                 SELECT m.id FROM msg_messages m
                                 JOIN srv_channels sc ON m.conversation_id = sc.conversation_id
                                 JOIN srv_members sm ON sc.server_id = sm.server_id
                                 WHERE sm.user_id = ?
                             )
                           )"""
            params = [user_id] + list(message_ids) + [user_id, user_id]

            clause, blocked_params = self._blocked_users_clause("r.user_id", user_id)
            query += clause
            params.extend(blocked_params)

            query += " GROUP BY r.message_id, r.emoji, r.is_custom, r.custom_emoji_id, e.url ORDER BY r.message_id, MIN(r.created_at)"

            rows = self._db.fetch_all(query, tuple(params))

            loaded: Dict[int, List[Dict[str, Any]]] = {mid: [] for mid in message_ids}
            for row in rows:
                msg_id = row["message_id"]
                count = row["count"]

                if count > 0:
                    loaded[msg_id].append(
                        {
                            "emoji": row["emoji"],
                            "count": count,
                            "me": bool(row["me"]),
                            "is_custom": bool(row["is_custom"]),
                            "custom_emoji_id": row["custom_emoji_id"],
                            "url": row.get("url"),
                        }
                    )
            return loaded

        key = reaction_read_key(
            "reactions_batch", user_id, *message_ids, credential=credential
        )
        cached = self._reaction_read_cache.get(key)
        if cached is _MISS:
            # Cold path: the aggregation query enforces the viewer's channel
            # access inline, so no separate gate query is needed here.
            cached = _load_batch()
            self._reaction_read_cache.set(key, cached)
            allowed_ids = None
        else:
            # LIVE GATE before serving a warm body: a revoked membership hides
            # its messages even though the memoized aggregation still has them.
            allowed_ids = self._accessible_reaction_message_ids(user_id, message_ids)

        result: Dict[int, List[Dict[str, Any]]] = {}
        for mid in message_ids:
            visible = allowed_ids is None or mid in allowed_ids
            result[mid] = list(cached.get(mid, [])) if visible else []
        return result

    def get_reaction_count(
        self,
        message_id: SnowflakeID,
        viewer_id: Optional[SnowflakeID] = None,
        credential: Optional[str] = None,
    ) -> int:
        def _load_count() -> int:
            query = "SELECT COUNT(*) as count FROM react_reactions WHERE message_id = ?"
            params: List[Any] = [message_id]

            # Keep internal counters consistent with the filtered list APIs.
            clause, blocked_params = self._blocked_users_clause("user_id", viewer_id)
            query += clause
            params.extend(blocked_params)

            row = self._db.fetch_one(query, tuple(params))
            return row["count"] if row else 0

        # ``viewer_id`` is part of the key: the block filter is viewer-specific.
        key = reaction_read_key(
            "reaction_count", viewer_id, message_id, credential=credential
        )
        return self._cached_reaction_read(key, _load_count)

    def get_reaction_count_by_emoji(
        self,
        message_id: SnowflakeID,
        emoji: str,
        viewer_id: Optional[SnowflakeID] = None,
        credential: Optional[str] = None,
    ) -> int:
        # Legacy/stored junk rows may no longer pass the stricter validator;
        # report an empty count instead of erroring reads.
        try:
            is_custom, custom_id, normalized = self._validate_emoji(emoji)
        except InvalidEmojiError:
            return 0

        def _load_count_by_emoji() -> int:
            if is_custom:
                query = "SELECT COUNT(*) as count FROM react_reactions WHERE message_id = ? AND custom_emoji_id = ?"
                params: List[Any] = [message_id, custom_id]
            else:
                query = "SELECT COUNT(*) as count FROM react_reactions WHERE message_id = ? AND emoji = ? AND is_custom = 0"
                params = [message_id, normalized]

            clause, blocked_params = self._blocked_users_clause("user_id", viewer_id)
            query += clause
            params.extend(blocked_params)

            row = self._db.fetch_one(query, tuple(params))
            return row["count"] if row else 0

        key = reaction_read_key(
            "reaction_count_emoji",
            viewer_id,
            message_id,
            normalized,
            credential=credential,
        )
        return self._cached_reaction_read(key, _load_count_by_emoji)

    def user_reacted_to_messages(
        self, user_id: int, message_ids: List[int]
    ) -> List[int]:
        if not message_ids:
            return []
        message_ids = validate_bulk_message_ids(message_ids)

        placeholders = ",".join("?" * len(message_ids))
        rows = self._db.fetch_all(
            f"SELECT DISTINCT message_id FROM react_reactions WHERE user_id = ? AND message_id IN ({placeholders})",
            (user_id, *message_ids),
        )
        return [row["message_id"] for row in rows]

    def get_users_who_reacted(
        self,
        user_id: int,
        message_id: SnowflakeID,
        emoji: str,
        credential: Optional[str] = None,
    ) -> List[SnowflakeID]:
        users = self.get_reaction_users(
            user_id=user_id,
            message_id=message_id,
            emoji=emoji,
            credential=credential,
        )
        return [u.user_id for u in users]

    def get_reactions_bulk(
        self,
        user_id: int,
        message_ids: List[int],
        credential: Optional[str] = None,
    ) -> Dict[int, List[Dict[str, Any]]]:
        return self.get_reactions_batch(
            user_id=user_id, message_ids=message_ids, credential=credential
        )
