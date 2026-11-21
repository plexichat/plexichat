"""
Participant service - Business logic for conversation participants.
"""

from typing import Any, Dict, List, Optional

from ..models import Participant, ParticipantRole
from ..repositories.participant import ParticipantRepository
from ..exceptions import (
    ConversationAccessDeniedError,
    ParticipantNotFoundError,
    ParticipantExistsError,
)
from .base import BaseService
from src.core.base import SnowflakeID


class ParticipantService(BaseService):
    """Service for participant operations."""

    def __init__(self, db: Any) -> None:
        super().__init__(db)
        self._repo = ParticipantRepository(db)

    def add_participant(
        self,
        conversation_id: SnowflakeID,
        user_id: SnowflakeID,
        role: ParticipantRole,
        auto_commit: bool = True,
    ) -> Participant:
        """Add a participant to a conversation."""
        if self._repo.exists(conversation_id, user_id):
            raise ParticipantExistsError("User is already a participant")

        now = self._get_timestamp()
        part_id = self._generate_id()

        self._repo.create(
            part_id, conversation_id, user_id, role, now, auto_commit=auto_commit
        )

        # ORPHAN-FIX: we no longer use the ``@cached`` decorator on
        # ``get_participant_ids`` (audit-trail: prior session dropped
        # it because the cache-key was conversation_id only, which
        # would collide with limit/offset variants). The
        # ``invalidate_pattern(f"participant_ids:*{cid}*")`` calls
        # in this service were the corresponding cache-evict pattern
        # and now invalidate nothing reachable. Drop them.
        self._cache_invalidate((conversation_id, user_id))

        row = self._repo.get_by_conversation_and_user(conversation_id, user_id)
        if row is None:
            raise ParticipantNotFoundError("Failed to create participant")
        return self._repo.row_to_model(row)

    def add_user_to_multiple_conversations(
        self,
        user_id: SnowflakeID,
        conversation_ids: List[SnowflakeID],
        role: ParticipantRole,
    ) -> None:
        """Add a user to multiple conversations in batch."""
        if not conversation_ids:
            return

        now = self._get_timestamp()
        participants_data = []

        for cid in conversation_ids:
            # We skip exists check for speed in batch, assuming caller knows what they are doing
            # or we catch duplicate errors. However, for "server join", we know they are new.
            # But to be safe and robust, we could check. But checking N times defeats the purpose.
            # Since this is used for "User joins Server", and they just joined, they shouldn't be in any server channels yet.
            participants_data.append(
                {
                    "id": self._generate_id(),
                    "conversation_id": cid,
                    "user_id": user_id,
                    "role": role,
                    "joined_at": now,
                }
            )

        self._repo.create_bulk(participants_data)

        # ORPHAN-FIX (see add_participant): the ``participant_ids``
        # Valkey pattern eviction was coupled to the dropped @cached
        # decorator and now hits Valkey for nothing reachable. Drop it.
        for cid in conversation_ids:
            self._cache_invalidate((cid, user_id))

    def remove_user_from_multiple_conversations(
        self,
        user_id: SnowflakeID,
        conversation_ids: List[SnowflakeID],
    ) -> None:
        """Remove a user from multiple conversations in batch."""
        if not conversation_ids:
            return

        self._repo.delete_bulk(user_id, conversation_ids)

        # ORPHAN-FIX (see add_participant): drop the now-unreachable
        # ``invalidate_pattern(f"participant_ids:*{cid}*")`` loop.
        for cid in conversation_ids:
            self._cache_invalidate((cid, user_id))

    def remove_participant(
        self,
        conversation_id: SnowflakeID,
        user_id: SnowflakeID,
        auto_commit: bool = True,
    ) -> None:
        """Remove a participant from a conversation."""
        self._repo.delete(conversation_id, user_id, auto_commit=auto_commit)
        self._cache_invalidate((conversation_id, user_id))
        # ORPHAN-FIX (see add_participant): drop the now-unreachable
        # ``invalidate_pattern(f"participant_ids:*{conversation_id}*")``.

    def get_participant(
        self, conversation_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[Participant]:
        """Get a participant record."""
        row = self._repo.get_by_conversation_and_user(conversation_id, user_id)
        return self._repo.row_to_model(row) if row else None

    def get_all_participants(self, conversation_id: SnowflakeID) -> List[Participant]:
        """Get all participants in a conversation."""
        rows = self._repo.get_all_by_conversation(conversation_id)
        return [self._repo.row_to_model(row) for row in rows]

    def get_participant_ids(
        self,
        conversation_id: SnowflakeID,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[SnowflakeID]:
        """Get all participant user IDs (for event routing).

        ``limit``: OOM FIX for power users with very large groups.
        Callers that only need a delivery fan-out subset (e.g.
        reactions, search-result enrichment) should pass a sensible
        ceiling. ``@cached`` is intentionally NOT applied here: the
        previous decorator-keyed on ``conversation_id`` alone, which
        caused a cache-key collision when callers asked for different
        ``limit``/``offset`` shapes and got back the wrong-shaped
        list. Callers that want caching should manage a TTL cache
        themselves at the call site; the route layer already does.
        """
        return self._repo.get_user_ids_by_conversation(
            conversation_id, limit=limit, offset=offset
        )

    def is_participant(
        self, conversation_id: SnowflakeID, user_id: SnowflakeID
    ) -> bool:
        """Check if user is a participant (with caching and server membership check)."""
        cache_key = (conversation_id, user_id)

        # Check cache first
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        import utils.logger as logger

        # Check direct participation
        if self._repo.exists(conversation_id, user_id):
            logger.debug(
                f"User {user_id} is a direct participant in conversation {conversation_id}"
            )
            self._cache_set(cache_key, True)
            return True

        # Check server membership for server channels
        metadata = self._repo.get_conversation_metadata(conversation_id)
        server_id = metadata.get("server_id") if metadata else None

        # Fallback: check srv_channels if metadata is missing/incomplete
        if not server_id:
            try:
                # Query srv_channels directly to see if this conversation belongs to a server
                # IMPORTANT: In server text channels, conversation_id is often the same as channel.id
                row = self._repo._fetch_one(
                    "SELECT server_id FROM srv_channels WHERE conversation_id = ? OR id = ?",
                    (conversation_id, conversation_id),
                )
                if row:
                    server_id = row["server_id"]
            except Exception as e:
                logger.debug(
                    f"Fallback server check failed for conversation {conversation_id}: {e}"
                )

        if server_id:
            try:
                is_member = self._repo.check_server_membership(int(server_id), user_id)
                if is_member:
                    logger.debug(
                        f"User {user_id} is a participant in conversation {conversation_id} via membership in server {server_id}"
                    )
                    self._cache_set(cache_key, True)
                    return True
                else:
                    logger.warning(
                        f"User {user_id} is NOT a member of server {server_id} associated with conversation {conversation_id}"
                    )
            except (TypeError, ValueError) as e:
                logger.error(
                    f"Error checking server membership for conversation {conversation_id}: {e}"
                )
        else:
            logger.debug(
                f"Conversation {conversation_id} has no associated server_id in metadata or srv_channels"
            )

        logger.warning(
            f"User {user_id} is NOT a participant in conversation {conversation_id} (direct or server-wide)"
        )
        self._cache_set(cache_key, False)
        return False

    # Every IN clause below is built from at most this many ids.  The list
    # endpoints that use this helper are page-capped (well under the limit),
    # but the bound keeps the method safe for any future caller.
    _BULK_PARTICIPATION_CHUNK = 256

    def are_participants_bulk(
        self, conversation_ids, user_id: SnowflakeID
    ) -> Dict[int, bool]:
        """Bulk counterpart of :meth:`is_participant` for list endpoints.

        The single-participant check resolves a conversation in up to four
        round trips (direct row, conversation metadata, ``srv_channels``
        fallback, server membership). ``get_recent_chats`` used to call it once
        per row, so a ten-row page cost ten independent rounds from a cold
        cache. This method mirrors that decision tree but collapses it into a
        bounded number of ``IN`` queries, and writes every result back into the
        same per-pair cache so a later single check cannot disagree with it.

        Only the *data shape* of the answer differs: a mapping of conversation
        id to the same yes/no decision ``is_participant`` would return. Callers
        that cannot supply a checker still fall back to the original per-row
        path, so no authorization decision is skipped when this helper is not
        available.
        """
        unique: List[int] = []
        seen = set()
        for value in conversation_ids:
            try:
                cid = int(value)
            except (TypeError, ValueError):
                continue
            if cid not in seen:
                seen.add(cid)
                unique.append(cid)
        if not unique:
            return {}

        allowed: Dict[int, bool] = {}
        pending: List[int] = []
        for cid in unique:
            cached = self._cache_get((cid, user_id))
            if cached is not None:
                allowed[cid] = bool(cached)
            else:
                pending.append(cid)

        for start in range(0, len(pending), self._BULK_PARTICIPATION_CHUNK):
            chunk = pending[start : start + self._BULK_PARTICIPATION_CHUNK]
            in_clause, params = self._repo._build_in_clause(chunk)
            rows = self._repo._fetch_all(
                "SELECT conversation_id FROM msg_participants "
                f"WHERE user_id = ? AND conversation_id IN {in_clause}",
                (user_id, *params),
            )
            direct = set()
            for row in rows:
                try:
                    conv_id = row["conversation_id"]
                except (KeyError, TypeError):
                    continue
                # SECURITY: only a genuine integer id from the live
                # participant row proves participation. Coercing a
                # malformed-but-coercible value with int() ("05"->5, 5.9->5,
                # True->1) would admit a conversation the row does not attest
                # to. Fail closed (type() also rejects bool).
                if type(conv_id) is int:
                    direct.add(conv_id)

            remaining: List[int] = []
            for cid in chunk:
                if cid in direct:
                    allowed[cid] = True
                    self._cache_set((cid, user_id), True)
                else:
                    remaining.append(cid)
            if not remaining:
                continue

            # Same server-membership fallback as is_participant, batched.
            # Conversation metadata wins where present; srv_channels is only
            # consulted for conversations whose metadata has no server_id.
            in_clause, params = self._repo._build_in_clause(remaining)
            meta_rows = self._repo._fetch_all(
                f"SELECT id, metadata FROM msg_conversations WHERE id IN {in_clause}",
                params,
            )
            meta_by_conv: Dict[int, Dict[str, Any]] = {}
            for row in meta_rows:
                meta = self._repo._json_loads(row.get("metadata"))
                if not isinstance(meta, dict):
                    continue
                try:
                    meta_by_conv[int(row["id"])] = meta
                except (KeyError, TypeError, ValueError):
                    continue

            missing_meta = [
                cid
                for cid in remaining
                if not (meta_by_conv.get(cid) or {}).get("server_id")
            ]
            by_channel_id: Dict[int, int] = {}
            by_conversation_id: Dict[int, int] = {}
            if missing_meta:
                in_clause, params = self._repo._build_in_clause(missing_meta)
                channel_rows = self._repo._fetch_all(
                    "SELECT id, conversation_id, server_id FROM srv_channels "
                    f"WHERE conversation_id IN {in_clause} OR id IN {in_clause}",
                    params + params,
                )
                for row in channel_rows:
                    raw_server_id = row.get("server_id")
                    if not raw_server_id:
                        continue
                    try:
                        server_id = int(raw_server_id)
                    except (TypeError, ValueError):
                        continue
                    for column, target in (
                        ("id", by_channel_id),
                        ("conversation_id", by_conversation_id),
                    ):
                        try:
                            target.setdefault(int(row[column]), server_id)
                        except (KeyError, TypeError, ValueError):
                            continue

            server_by_conv: Dict[int, int] = {}
            server_ids = set()
            for cid in remaining:
                meta = meta_by_conv.get(cid) or {}
                server_id = (
                    meta.get("server_id")
                    or by_channel_id.get(cid)
                    or by_conversation_id.get(cid)
                )
                if not server_id:
                    continue
                try:
                    server_by_conv[cid] = int(server_id)
                    server_ids.add(int(server_id))
                except (TypeError, ValueError):
                    # A non-numeric server id can never match a membership row;
                    # is_participant also denies in this case.
                    continue

            member_servers = set()
            if server_ids:
                in_clause, params = self._repo._build_in_clause(sorted(server_ids))
                member_rows = self._repo._fetch_all(
                    f"SELECT server_id FROM srv_members WHERE user_id = ? "
                    f"AND server_id IN {in_clause}",
                    (user_id, *params),
                )
                for row in member_rows:
                    try:
                        member_servers.add(int(row["server_id"]))
                    except (KeyError, TypeError, ValueError):
                        continue

            for cid in remaining:
                permitted = server_by_conv.get(cid) in member_servers
                allowed[cid] = permitted
                self._cache_set((cid, user_id), permitted)

        return allowed

    def update_role(
        self,
        conversation_id: SnowflakeID,
        user_id: SnowflakeID,
        role: ParticipantRole,
        auto_commit: bool = True,
    ) -> Participant:
        """Update a participant's role."""
        self._repo.update_role(conversation_id, user_id, role, auto_commit=auto_commit)

        row = self._repo.get_by_conversation_and_user(conversation_id, user_id)
        if row is None:
            raise ParticipantNotFoundError("Participant not found")
        return self._repo.row_to_model(row)

    def update_mute(
        self,
        conversation_id: SnowflakeID,
        user_id: SnowflakeID,
        muted: bool,
        muted_until: Optional[int] = None,
        auto_commit: bool = True,
    ) -> None:
        """Update participant mute status."""
        self._repo.update_mute(
            conversation_id, user_id, muted, muted_until, auto_commit=auto_commit
        )

    def update_last_read(
        self,
        conversation_id: SnowflakeID,
        user_id: SnowflakeID,
        last_read_message_id: SnowflakeID,
        auto_commit: bool = True,
    ) -> None:
        """Update participant's last read position."""
        now = self._get_timestamp()
        self._repo.update_last_read(
            conversation_id, user_id, last_read_message_id, now, auto_commit=auto_commit
        )

    def find_next_owner(
        self, conversation_id: SnowflakeID, exclude_user_id: SnowflakeID
    ) -> Optional[SnowflakeID]:
        """Find next suitable owner when current owner leaves."""
        return self._repo.find_next_owner(conversation_id, exclude_user_id)

    def check_permission(
        self,
        conversation_id: SnowflakeID,
        user_id: SnowflakeID,
        required_roles: List[ParticipantRole],
    ) -> bool:
        """Check if user has one of the required roles."""
        participant = self.get_participant(conversation_id, user_id)
        if not participant:
            return False
        return participant.role in required_roles

    def require_permission(
        self,
        conversation_id: SnowflakeID,
        user_id: SnowflakeID,
        required_roles: List[ParticipantRole],
        error_message: str = "Permission denied",
    ) -> Participant:
        """Require user to have one of the required roles, raise if not."""
        participant = self.get_participant(conversation_id, user_id)
        if not participant:
            raise ConversationAccessDeniedError(
                "Not a participant in this conversation"
            )
        if participant.role not in required_roles:
            raise ConversationAccessDeniedError(error_message)
        return participant
