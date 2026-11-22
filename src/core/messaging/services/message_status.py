"""
Message status service - Business logic for delivery/read status.
"""

from typing import Any, Dict, List, Optional, Tuple

from src.core.database import cached
from ..models import MessageStatus, MessageStatusType
from ..repositories.message_status import MessageStatusRepository
from ..repositories.message import MessageRepository
from ..repositories.participant import ParticipantRepository
from ..exceptions import (
    ConversationAccessDeniedError,
    MessageNotFoundError,
    MessageAccessDeniedError,
)
from .base import BaseService
from .participant import ParticipantService
from .user_settings import UserSettingsService
from . import unread_cache
from src.core.base import SnowflakeID

# Bound every IN list built by the delivery-ack path. SQLite/Postgres cap
# host parameters, and a burst must degrade to a few round trips rather than
# one unbounded query (or the old one-query-per-message fan-out).
_DELIVERY_BATCH_CHUNK = 500

# A caller whose status is already delivered/read needs no new row. The batch
# status lookup returns enum members; the legacy per-id probe returns the raw
# string, so accept both shapes.
_DONE_DELIVERY_STATUSES = frozenset(
    {
        MessageStatusType.DELIVERED,
        MessageStatusType.READ,
        MessageStatusType.DELIVERED.value,
        MessageStatusType.READ.value,
    }
)


class MessageStatusService(BaseService):
    """Service for message status operations."""

    def __init__(
        self,
        db: Any,
        participant_service: ParticipantService,
        user_settings_service: UserSettingsService,
    ) -> None:
        super().__init__(db)
        self._repo = MessageStatusRepository(db)
        self._message_repo = MessageRepository(db)
        self._participant_repo = ParticipantRepository(db)
        self._participant_svc = participant_service
        self._user_settings_svc = user_settings_service

    def _get_servers_module(self):
        """Return the servers module without importing the whole API app.

        Importing ``src.api`` pulls in the full FastAPI application (every
        router, schema and the gateway) — a ~2.5s import cost on first use
        per worker. When ``src.api`` has already been imported we return its
        configured object (exactly what ``api.get_servers()`` returned
        before); otherwise we fall back to the light ``src.core.servers``
        import, which is the same fallback ``api.get_servers()`` itself
        uses. This preserves identical semantics in every context.
        """
        import sys

        api_mod = sys.modules.get("src.api")
        if api_mod is not None:
            get_servers = getattr(api_mod, "get_servers", None)
            if get_servers is not None:
                servers = get_servers()
                if servers:
                    return servers
        try:
            from src.core import servers

            return servers
        except ImportError:
            return None

    def _invalidate_unread_counts(self, user_id: SnowflakeID) -> None:
        """Drop every cached unread-count view for one user.

        The ``GET /users/@me/unread`` route is decorated
        ``@cached(ttl=10, prefix="unread_counts_api")`` and its key embeds the
        caller's ``TokenInfo`` as
        ``unread_counts_api:current_user:TokenInfo:<user_id>``. Every other
        read-state cache (``msg_reader_ids:*`` etc.) is invalidated when read
        state changes; without this one the unread badge kept serving the
        pre-ack count until the entry's TTL expired.

        The bounded service-level aggregate (used by the uncached per-channel
        unread endpoint) is dropped here as well so both views advance
        together on a read-state transition.
        """
        try:
            from src.core.database import invalidate_pattern

            invalidate_pattern(f"unread_counts_api:current_user:TokenInfo:{user_id}")
        except Exception as cache_error:
            import utils.logger as logger

            logger.debug(f"unread count cache invalidate failed: {cache_error}")
        try:
            unread_cache.invalidate_user(user_id)
        except Exception as cache_error:
            import utils.logger as logger

            logger.debug(f"unread aggregate invalidate failed: {cache_error}")

    def _delivery_participation(
        self, user_id: SnowflakeID, conversation_ids: List[SnowflakeID]
    ) -> Dict[SnowflakeID, bool]:
        """Resolve the access gate once per distinct conversation.

        The bulk helper mirrors ``is_participant``'s decision tree but uses
        bounded ``IN`` queries, and writes each answer back into the same
        per-pair cache the single check reads -- so a revoked membership can
        never survive into a later delivery write. Both the missing-helper and
        the error path fall back to the single check and fail closed (an
        unresolved conversation is treated as "not a participant").
        """
        bulk = getattr(self._participant_svc, "are_participants_bulk", None)
        if callable(bulk):
            try:
                result = bulk(conversation_ids, user_id)
                if isinstance(result, dict):
                    return {
                        int(cid): bool(result.get(cid, False))
                        for cid in conversation_ids
                    }
            except Exception as e:
                import utils.logger as logger

                logger.debug(f"bulk delivery participation failed: {e}")

        result = {}
        for cid in conversation_ids:
            try:
                result[int(cid)] = bool(
                    self._participant_svc.is_participant(cid, user_id)
                )
            except Exception as e:
                import utils.logger as logger

                logger.debug(f"delivery participation check failed: {e}")
                result[int(cid)] = False
        return result

    def _delivery_existing_statuses(
        self, user_id: SnowflakeID, message_ids: List[SnowflakeID]
    ) -> Dict[SnowflakeID, Any]:
        """Fetch this caller's prior statuses with bounded batch queries."""
        existing: Dict[SnowflakeID, Any] = {}
        for start in range(0, len(message_ids), _DELIVERY_BATCH_CHUNK):
            chunk = message_ids[start : start + _DELIVERY_BATCH_CHUNK]
            try:
                batch = self._repo.get_batch_for_user(user_id, chunk)
                if isinstance(batch, dict):
                    for mid, status in batch.items():
                        try:
                            existing[int(mid)] = status
                        except (TypeError, ValueError):
                            continue
                    continue
            except Exception as e:
                import utils.logger as logger

                logger.debug(f"bulk delivery status lookup failed: {e}")
            # Legacy/stub repo (or a failed batch): per-id probe, already
            # scoped to this caller.
            for mid in chunk:
                row = self._repo.get_by_message_and_user(mid, user_id)
                if row:
                    existing[int(mid)] = row.get("status")
        return existing

    def mark_delivered(
        self, user_id: SnowflakeID, message_ids: List[SnowflakeID]
    ) -> int:
        """Mark messages as delivered with bounded round trips.

        BEFORE: one message-row SELECT, one status probe and one participation
        probe per id -- a 3N fan-out on a delivery burst. AFTER: message rows
        and the caller's prior statuses are each fetched with one bounded IN
        query per chunk, and participation is resolved once per distinct
        conversation through the shared bulk gate. Every access decision still
        runs before a write and fails closed, so a revoked membership or a
        status from another user can never grant or suppress a delivery.
        """
        if not message_ids:
            return 0

        # Normalize and de-duplicate while preserving caller order. Unparsable
        # ids can never match a row and are dropped before any query.
        unique_ids: List[int] = []
        seen = set()
        for raw in message_ids:
            try:
                mid = int(raw)
            except (TypeError, ValueError):
                continue
            if mid in seen:
                continue
            seen.add(mid)
            unique_ids.append(mid)
        if not unique_ids:
            return 0

        now = self._get_timestamp()

        # 1. Message rows: one IN query per bounded chunk.
        rows_by_id: Dict[int, Dict[str, Any]] = {}
        for start in range(0, len(unique_ids), _DELIVERY_BATCH_CHUNK):
            chunk = unique_ids[start : start + _DELIVERY_BATCH_CHUNK]
            for row in self._message_repo.get_batch_by_ids(chunk):
                try:
                    rows_by_id[int(row["id"])] = row
                except (KeyError, TypeError, ValueError):
                    continue

        # 2. Cheap local filters: existing row, not authored by caller, not a
        # system message. The conversation gate is deferred until its ids are
        # distinct so it is paid once, not once per message.
        eligible: List[int] = []
        conversation_ids: List[int] = []
        seen_convs = set()
        for mid in unique_ids:
            row = rows_by_id.get(mid)
            if not row or row["author_id"] == user_id or row["author_id"] == 0:
                continue
            eligible.append(mid)
            try:
                cid = int(row["conversation_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if cid not in seen_convs:
                seen_convs.add(cid)
                conversation_ids.append(cid)

        if not eligible:
            return 0

        # 3. Authorization: fail closed for any conversation we cannot resolve.
        participation = self._delivery_participation(user_id, conversation_ids)

        # 4. Skip messages this caller has already received (read wins).
        existing = self._delivery_existing_statuses(user_id, eligible)

        valid_ids = []
        for mid in eligible:
            row = rows_by_id[mid]
            try:
                cid = int(row["conversation_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if not participation.get(cid, False):
                continue
            if existing.get(mid) in _DONE_DELIVERY_STATUSES:
                continue
            valid_ids.append(mid)

        if not valid_ids:
            return 0

        return self._repo.batch_mark_delivered(user_id, valid_ids, now)

    def mark_read(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        up_to_message_id: Optional[SnowflakeID] = None,
    ) -> int:
        """Mark messages as read (batch operation)."""
        # Get participant to check current last_read_message_id
        participant = self._participant_svc.get_participant(conversation_id, user_id)
        if not participant:
            # Fallback to is_participant check if get_participant fails (though it shouldn't for a member)
            if not self._participant_svc.is_participant(conversation_id, user_id):
                raise ConversationAccessDeniedError(
                    "Not a participant in this conversation"
                )
            last_read_id = 0
        else:
            last_read_id = participant.last_read_message_id or 0

        # Determine target message ID
        target_msg_id = up_to_message_id
        if not target_msg_id:
            target_msg_id = self._message_repo.get_max_id_in_conversation(
                conversation_id
            )

        # Skip if no new messages to mark
        if not target_msg_id or target_msg_id <= last_read_id:
            return 0

        now = self._get_timestamp()

        # Only update public read statuses if user has read receipts enabled AND channel has them enabled
        user_settings = self._user_settings_svc.get_message_settings(user_id)

        # Check if channel has read receipts enabled
        channel_receipts_enabled = True
        try:
            # We need to find if this is a server channel and check its setting
            servers_mod = self._get_servers_module()
            if servers_mod:
                # messaging conversation_id might be different from server channel_id
                # but servers_mod.get_channel usually handles the lookup
                channel = servers_mod.get_channel(conversation_id, user_id)
                if channel:
                    channel_receipts_enabled = getattr(
                        channel, "read_receipts_enabled", True
                    )
        except Exception as channel_error:
            import utils.logger as logger

            logger.debug(f"read-receipt channel lookup failed: {channel_error}")

        count = 0
        if user_settings.read_receipts_enabled and channel_receipts_enabled:
            # Pass last_read_id to avoid redundant updates
            count = self._repo.batch_mark_read(
                user_id,
                conversation_id,
                target_msg_id,
                now,
                start_from_id=last_read_id,
            )

            if count > 0:
                # Invalidate reader IDs and status batch cache for ALL users
                try:
                    from src.core.database import invalidate_pattern

                    # Key format: msg_reader_ids:* and msg_status_batch:*
                    invalidate_pattern("msg_reader_ids:*")
                    invalidate_pattern("msg_status_batch:*")

                    # Invalidate the individual message object caches for
                    # messages in this conversation only
                    invalidate_pattern(f"msg:obj:*:{conversation_id}:*")
                    # And the recent messages list cache
                    invalidate_pattern(f"msg:recent:{conversation_id}")

                    # ALSO invalidate the message list caches for the API
                    # and internal repo
                    self._message_repo.invalidate_conversation_cache(conversation_id)
                except Exception as cache_error:
                    import utils.logger as logger

                    logger.debug(f"conversation cache invalidate failed: {cache_error}")

        # Update participant's last read position (always, for unread counts)
        self._participant_repo.update_last_read(
            conversation_id, user_id, target_msg_id, now
        )

        # Drop the cached /users/@me/unread counts so the badge reflects the
        # ack immediately instead of after the 10s TTL.
        self._invalidate_unread_counts(user_id)

        return count

    def _invalidate_bulk_read_cache(
        self, user_id: SnowflakeID, conversation_ids: List[SnowflakeID]
    ) -> None:
        """Invalidate read-state caches once for a whole bulk ack."""
        try:
            from src.core.database import invalidate_pattern

            invalidate_pattern("msg_reader_ids:*")
            invalidate_pattern("msg_status_batch:*")
            for cid in conversation_ids:
                invalidate_pattern(f"msg:obj:*:{cid}:*")
                invalidate_pattern(f"msg:recent:{cid}")
                self._message_repo.invalidate_conversation_cache(cid)
        except Exception as cache_error:
            import utils.logger as logger

            logger.debug(f"bulk conversation cache invalidate failed: {cache_error}")
        self._invalidate_unread_counts(user_id)

    def mark_read_bulk(
        self,
        user_id: SnowflakeID,
        updates: List[Tuple[SnowflakeID, Optional[SnowflakeID]]],
    ) -> Dict[SnowflakeID, int]:
        """Mark messages read across many channels with bounded round trips.

        BEFORE: the bulk-ack route called ``mark_read`` once per channel, and
        each call ran its own participant lookup, MAX(id), settings/channel
        probes, message SELECT and executemany — up to ~100x the work.

        AFTER: participation, last-read positions and per-channel MAX(id) are
        each resolved with one chunked query, all status rows are written by
        one chunked executemany, and all participant positions by one more.
        Per-channel read-receipt settings and cache invalidation are preserved.
        """
        by_conv: Dict[Any, Optional[SnowflakeID]] = {}
        for conversation_id, up_to in updates:
            if conversation_id is None:
                continue
            try:
                key: Any = int(conversation_id)
            except (TypeError, ValueError):
                key = conversation_id
            by_conv[key] = up_to
        if not by_conv:
            return {}

        # Resolve the access gate in bounded batches. ``is_participant`` is
        # cached per (conversation, user) but is still one or more DB round
        # trips per conversation from a cold cache; a server-wide ack arrives
        # cold and used to pay that N times. The bulk helper mirrors the same
        # decision tree, writes results back into the same per-pair cache and
        # fails closed for anything it cannot resolve, so authorization is
        # unchanged -- only the number of round trips is bounded.
        participation = self._delivery_participation(user_id, list(by_conv))
        eligible = [conv for conv in by_conv if participation.get(conv, False)]
        if not eligible:
            return {}

        last_read_map = self._participant_repo.get_last_read_positions(
            user_id, eligible
        )
        max_id_map = self._message_repo.get_max_ids_in_conversations(eligible)

        targets: Dict[Any, Tuple[SnowflakeID, SnowflakeID]] = {}
        for conv in eligible:
            target = by_conv[conv]
            if target is None:
                target = max_id_map.get(conv)
            if target is None:
                continue
            try:
                target_value = int(target)
                last_value = int(last_read_map.get(conv) or 0)
            except (TypeError, ValueError):
                continue
            if target_value <= last_value:
                continue
            targets[conv] = (target, last_value)

        counts: Dict[SnowflakeID, int] = {conv: 0 for conv in by_conv}
        if not targets:
            return counts

        now = self._get_timestamp()
        user_settings = self._user_settings_svc.get_message_settings(user_id)
        servers_mod = self._get_servers_module()

        ranges = []
        for conv, (target, last_value) in targets.items():
            receipts_enabled = True
            if servers_mod:
                try:
                    channel = servers_mod.get_channel(conv, user_id)
                    if channel:
                        receipts_enabled = getattr(
                            channel, "read_receipts_enabled", True
                        )
                except Exception:
                    receipts_enabled = True
            if user_settings.read_receipts_enabled and receipts_enabled:
                ranges.append((conv, target, last_value))

        if ranges:
            counts.update(self._repo.batch_mark_read_bulk(user_id, ranges, now))

        # Read position always advances, even when public receipts are off.
        self._participant_repo.update_last_read_bulk(
            [(conv, user_id, target, now) for conv, (target, _) in targets.items()]
        )
        self._invalidate_bulk_read_cache(user_id, list(targets))
        return counts

    def get_unread_count(
        self, user_id: SnowflakeID, conversation_id: Optional[SnowflakeID] = None
    ) -> Dict[SnowflakeID, int]:
        """Get unread message counts.

        Unread counts are read repeatedly for the same user in a client sync
        burst: the aggregate ``/users/@me/unread`` call plus one
        ``/channels/{id}/messages/unread`` call per open channel. Each used to
        run its own COUNT / GROUP BY over ``msg_participants`` joined with
        ``msg_messages``. The bounded aggregate cache collapses those repeats
        into a single computation while the read state is unchanged; entries
        are invalidated eagerly on read-state writes (below) and on message
        writes (``SendMixin.send_message``).
        """
        if conversation_id:
            # Normalize IDs for SQLite bindings; in some call paths these can be
            # str-like SnowflakeIDs which may not match integer columns.
            try:
                conv_id_norm: SnowflakeID = int(conversation_id)
            except Exception:
                conv_id_norm = conversation_id

            try:
                user_id_norm: SnowflakeID = int(user_id)
            except Exception:
                user_id_norm = user_id

            def participant_exists() -> bool:
                return self._participant_repo.exists(conv_id_norm, user_id_norm)

            # IMPORTANT: Use a direct DB-backed participation check here.
            # ParticipantService.is_participant has an internal cache and also
            # server-membership logic; both can cause false negatives for DMs in
            # isolated test flows. Keep the check ahead of the cache lookup so
            # a user removed from a conversation can never read a stale count.
            if not participant_exists():
                return {}

            cached_count = unread_cache.get_one(user_id_norm, conv_id_norm)
            if cached_count is not None:
                return {conv_id_norm: cached_count}

            # A prior aggregate read may already hold this conversation.
            cached_all = unread_cache.get_all(user_id_norm)
            if cached_all is not None:
                count = cached_all.get(conv_id_norm, 0)
                unread_cache.put_one(user_id_norm, conv_id_norm, count)
                return {conv_id_norm: count}

            count = self._repo.get_unread_count(user_id_norm, conv_id_norm)
            unread_cache.put_one(user_id_norm, conv_id_norm, count)
            return {conv_id_norm: count}

        cached_all = unread_cache.get_all(user_id)
        if cached_all is not None:
            return cached_all

        counts = self._repo.get_all_unread_counts(user_id)
        unread_cache.put_all(user_id, counts)
        return counts

    def get_message_status(
        self, user_id: SnowflakeID, message_id: SnowflakeID
    ) -> List[MessageStatus]:
        """Get delivery/read status for a message (sender only)."""
        msg_row = self._message_repo.get_by_id(message_id)
        if not msg_row:
            raise MessageNotFoundError("Message not found")

        # Security: only the author of the message can view its status details
        if msg_row["author_id"] != user_id:
            raise MessageAccessDeniedError("Only sender can view message status")

        rows = self._repo.get_all_by_message(message_id)
        return [self._repo.row_to_model(row) for row in rows]

    def get_reader_ids(
        self, user_id: SnowflakeID, message_id: SnowflakeID
    ) -> List[SnowflakeID]:
        """Get IDs of users who have read a message (sender only)."""
        msg_row = self._message_repo.get_by_id(message_id)
        if not msg_row:
            return []

        # Security: only the author can see who read their message
        if msg_row["author_id"] != user_id:
            return []

        return self._repo.get_reader_ids(message_id)

    @cached(ttl=30, prefix="msg_reader_ids")
    def get_batch_reader_ids(
        self, user_id: SnowflakeID, message_ids: List[SnowflakeID]
    ) -> Dict[SnowflakeID, List[SnowflakeID]]:
        """Get IDs of users who have read messages (batch, sender only)."""
        if not message_ids:
            return {}

        # Get message rows to verify ownership
        msg_rows = self._message_repo.get_batch_by_ids(list(message_ids))

        # Filter for messages where user is the author
        owned_message_ids = [
            row["id"] for row in msg_rows if row["author_id"] == user_id
        ]

        if not owned_message_ids:
            return {mid: [] for mid in message_ids}

        reader_map = self._repo.get_batch_reader_ids(owned_message_ids)

        # Ensure all requested IDs are in the result
        result: Dict[SnowflakeID, List[SnowflakeID]] = {mid: [] for mid in message_ids}
        result.update(reader_map)
        return result

    @cached(ttl=30, prefix="msg_status_batch")
    def get_batch_status_info(
        self, user_id: SnowflakeID, message_ids: List[SnowflakeID]
    ) -> Dict[SnowflakeID, Dict[str, Any]]:
        """Get status info for multiple messages (batch operation)."""
        if not message_ids:
            return {}

        import utils.logger as logger

        status_map = self._repo.get_batch_for_user(user_id, message_ids)
        counts_map = self._repo.get_batch_counts(message_ids)

        # Batch fetch message rows to check channel settings
        msg_rows = self._message_repo.get_batch_by_ids(message_ids)
        msg_conv_map = {row["id"]: row["conversation_id"] for row in msg_rows}

        # Cache for channel settings
        channel_settings_cache = {}  # {conv_id: bool}
        servers_mod = self._get_servers_module()

        result: Dict[SnowflakeID, Dict[str, Any]] = {}
        for mid in message_ids:
            conv_id = msg_conv_map.get(mid)
            receipts_enabled = True
            if conv_id and servers_mod:
                if conv_id not in channel_settings_cache:
                    try:
                        channel = servers_mod.get_channel(conv_id, user_id)
                        channel_settings_cache[conv_id] = getattr(
                            channel, "read_receipts_enabled", True
                        )
                    except Exception:
                        channel_settings_cache[conv_id] = True
                receipts_enabled = channel_settings_cache[conv_id]

            stats = counts_map.get(mid, {"delivery_count": 0, "read_count": 0})

            # If receipts disabled for channel, treat as 0
            if not receipts_enabled:
                stats = {"delivery_count": 0, "read_count": 0}

            # Determine overall status: if read_count > 0, it's READ.
            # If delivery_count > 0, it's DELIVERED. Otherwise it's the user's own status.
            overall_status = status_map.get(mid, MessageStatusType.SENT)
            if stats["read_count"] > 0:
                overall_status = MessageStatusType.READ
            elif stats["delivery_count"] > 0:
                # Only upgrade to DELIVERED if it was just SENT
                if overall_status == MessageStatusType.SENT:
                    overall_status = MessageStatusType.DELIVERED

            result[mid] = {
                "status": overall_status,
                "delivery_count": stats["delivery_count"],
                "read_count": stats["read_count"],
            }

            if stats["read_count"] > 0:
                logger.debug(
                    f"Status Info: Message {mid} for user {user_id} has read_count {stats['read_count']}, overall={overall_status}"
                )

        return result

    def get_last_read(
        self, user_id: SnowflakeID, conversation_id: SnowflakeID
    ) -> Dict[str, Any]:
        """Get the last read position for a user in a conversation."""
        participant = self._participant_svc.get_participant(conversation_id, user_id)
        if not participant:
            if not self._participant_svc.is_participant(conversation_id, user_id):
                raise ConversationAccessDeniedError(
                    "Not a participant in this conversation"
                )
            return {"last_read_message_id": None, "last_read_at": None}

        return {
            "last_read_message_id": participant.last_read_message_id,
            "last_read_at": participant.last_read_at,
        }

    def mark_unread(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        up_to_message_id: Optional[SnowflakeID] = None,
    ) -> int:
        """Mark messages as unread by rolling back last_read_message_id.

        If up_to_message_id is provided, sets last_read to the message before that one.
        If not provided, rolls back to the message before the latest message.

        Returns the new unread count.
        """
        participant = self._participant_svc.get_participant(conversation_id, user_id)
        if not participant:
            if not self._participant_svc.is_participant(conversation_id, user_id):
                raise ConversationAccessDeniedError(
                    "Not a participant in this conversation"
                )
            # No read state yet — nothing to mark unread
            return 0

        # Determine target message ID for new last_read
        target_msg_id: SnowflakeID
        if up_to_message_id:
            # Roll back to the message *before* the given one, not the given
            # one itself. Callers crossing a JSON boundary may hand us a
            # decimal string, so normalize it for the comparison; anything
            # non-numeric is passed through and simply matches no row.
            anchor_id: Any
            try:
                anchor_id = int(up_to_message_id)
            except (TypeError, ValueError):
                anchor_id = up_to_message_id
            prev_id = self._message_repo.get_previous_id_in_conversation(
                conversation_id, anchor_id
            )
            target_msg_id = prev_id if prev_id is not None else 0
        else:
            # Roll back to the message before the most recent one. Snowflake
            # IDs are sparse, so max_id - 1 does not address a real message.
            max_id = self._message_repo.get_max_id_in_conversation(conversation_id)
            if not max_id:
                return 0
            prev_id = self._message_repo.get_previous_id_in_conversation(
                conversation_id, max_id
            )
            target_msg_id = prev_id if prev_id is not None else 0

        now = self._get_timestamp()

        # Update participant's last read position to the target
        self._participant_repo.update_last_read(
            conversation_id, user_id, target_msg_id, now
        )

        # Rolling read state back changes the unread badge too.
        self._invalidate_unread_counts(user_id)

        # Recalculate unread count
        new_count = self._repo.get_unread_count(user_id, conversation_id)

        return new_count

    def mark_all_server_read(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
    ) -> int:
        """Mark all channels in a server as read in bounded round trips.

        BEFORE: this looped over every channel and called the full
        ``mark_read`` once per channel. Each call re-ran a participant lookup,
        MAX(id), settings/channel probes, a status SELECT + executemany,
        several cache invalidations and a read-position write -- an O(N)
        fan-out where N is the number of channels in the server.

        AFTER: every channel conversation id is collected in one pass and
        handed to ``mark_read_bulk`` in a single call. That path chunks
        participation, last-read positions, MAX(id), status writes and
        read-position updates into a bounded number of round trips, applies
        the same owner-scoped / fail-closed participation gate, and preserves
        per-channel read-receipt settings. The returned count keeps its
        historical meaning: the number of channels the caller can actually
        read (is a participant of).
        """
        servers_mod = self._get_servers_module()
        if not servers_mod:
            return 0

        try:
            channels = servers_mod.get_channels(server_id, user_id)
        except Exception:
            # Not a member or server inaccessible — nothing to mark
            return 0

        # Resolve every channel's conversation id once, de-duplicated so a
        # channel aliasing another cannot make the bulk pass do the same work
        # twice. Ids are normalised to int for stable cache keys / IN binds.
        conversation_ids: List[SnowflakeID] = []
        seen = set()
        for channel in channels:
            conv_id = getattr(channel, "conversation_id", None)
            if not conv_id:
                continue
            try:
                conv = int(conv_id)
            except (TypeError, ValueError):
                continue
            if conv in seen:
                continue
            seen.add(conv)
            conversation_ids.append(conv)

        if not conversation_ids:
            return 0

        # One batched pass replaces the per-channel full mark_read loop. With
        # no explicit up_to id, mark_read_bulk resolves MAX(id) per conversation
        # itself, matching the old behaviour exactly.
        self.mark_read_bulk(user_id, [(cid, None) for cid in conversation_ids])

        # ``channels_marked`` keeps its old semantics: channels the caller is
        # allowed to read. mark_read_bulk applied that same fail-closed gate,
        # and is_participant is cache-backed and already warm from the bulk
        # pass, so this costs no extra database round trips.
        return sum(
            1
            for cid in conversation_ids
            if self._participant_svc.is_participant(cid, user_id)
        )

    def create_initial_status(
        self,
        message_id: SnowflakeID,
        user_id: SnowflakeID,
        auto_commit: bool = True,
    ) -> None:
        """Create initial sent status for a message."""
        now = self._get_timestamp()
        status_id = self._generate_id()
        self._repo.create(
            status_id, message_id, user_id, MessageStatusType.SENT, now, auto_commit
        )
