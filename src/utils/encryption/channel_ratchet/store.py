"""
Persistence layer for the channel ratchet.

The store is the only place that touches the
``channel_ratchet_intervals`` table directly. Higher-level code
goes through :class:`ChannelRatchetManager` which composes the
store with the keyring and the HKDF primitive.
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from typing import List, Optional, Tuple

from typing import TYPE_CHECKING

from .exceptions import RatchetKeyWrapError, RatchetIntervalNotFoundError
from .interval import RatchetInterval

if TYPE_CHECKING:
    from src.core.base import SnowflakeID


# A channel message list decrypts every ENC:3: envelope through get_by_id().
# All messages on a page reference the same handful of intervals, so an
# uncached store issues one identical point-read per message (an N+1). Bound
# the read-through cache so the common list/search/pins path pays one read per
# interval while memory stays bounded even for an adversarial id sweep.
_INTERVAL_READ_CACHE_MAX = 512
_INTERVAL_READ_CACHE_TTL_SECONDS = 60.0


class ChannelRatchetStore:
    """CRUD operations for the ``channel_ratchet_intervals`` table.

    The store does not know about HKDF or AES-GCM; it only deals with
    opaque, keyring-wrapped ``start_key`` blobs. Rotation, splitting,
    and encryption live in :class:`ChannelRatchetManager`.
    """

    def __init__(self, db, keyring) -> None:
        self._db = db
        self._keyring = keyring
        self._lock = threading.RLock()
        # Bounded, short-TTL read-through cache for point lookups. Keyed by
        # genuine ``int`` interval ids only; values are ``(expiry, interval)``.
        # Writers invalidate the affected id so no stale range/key is served.
        self._interval_cache: "OrderedDict[int, Tuple[float, RatchetInterval]]" = (
            OrderedDict()
        )

    def _invalidate_interval_cache(self, interval_id) -> None:
        """Drop one interval's cached row after a mutation.

        Only ids that are genuine ``int`` values are cached (see get_by_id),
        so a non-int spelling can never leave a stale entry behind.
        """
        if type(interval_id) is int:
            self._interval_cache.pop(interval_id, None)

    @staticmethod
    def _table_exists(db) -> bool:
        return bool(db.table_exists("channel_ratchet_intervals"))

    def _wrap(self, raw_key: bytes) -> str:
        """Encrypt a start key for storage using the existing keyring."""
        try:
            return self._keyring.wrap(raw_key)
        except Exception as exc:
            raise RatchetKeyWrapError(f"failed to wrap start key: {exc}") from exc

    def _unwrap(self, wrapped: str) -> bytes:
        """Decrypt a stored start key."""
        try:
            return self._keyring.unwrap(wrapped)
        except Exception as exc:
            raise RatchetKeyWrapError(f"failed to unwrap start key: {exc}") from exc

    def get_active(self, conversation_id: SnowflakeID) -> Optional[RatchetInterval]:
        """Return the currently-open interval for ``conversation_id``.

        An interval is "active" when ``end_message_id`` is NULL.
        """
        with self._lock:
            if not self._table_exists(self._db):
                return None
            row = self._db.fetch_one(
                """
                SELECT interval_id, conversation_id, start_message_id,
                       end_message_id, start_key_wrapped, created_at,
                       last_message_at
                FROM channel_ratchet_intervals
                WHERE conversation_id = ? AND end_message_id IS NULL
                ORDER BY start_message_id DESC
                LIMIT 1
                """,
                (conversation_id,),
            )
            if not row:
                return None
            return self._row_to_interval(row)

    def get_by_id(self, interval_id: SnowflakeID) -> Optional[RatchetInterval]:
        """Return a single interval by id, or None if it does not exist.

        Read-through cached so a message list decrypting N envelopes that
        share an interval issues ONE point-read instead of N. The key is a
        strict ``int``: coercing a string/bool here would let a caller supply
        a different type that aliases an existing entry. Non-``int`` inputs
        bypass the cache entirely and keep the original query/param semantics
        (they are still passed to the DB verbatim). The TTL is a safety net;
        every write that mutates an interval also invalidates it explicitly.
        """
        with self._lock:
            cache_key = interval_id if type(interval_id) is int else None
            if cache_key is not None:
                hit = self._interval_cache.get(cache_key)
                if hit is not None:
                    expires_at, interval = hit
                    if expires_at > time.monotonic():
                        self._interval_cache.move_to_end(cache_key)
                        return interval
                    self._interval_cache.pop(cache_key, None)

            if not self._table_exists(self._db):
                return None
            row = self._db.fetch_one(
                """
                SELECT interval_id, conversation_id, start_message_id,
                       end_message_id, start_key_wrapped, created_at,
                       last_message_at
                FROM channel_ratchet_intervals
                WHERE interval_id = ?
                """,
                (interval_id,),
            )
            if not row:
                return None
            interval = self._row_to_interval(row)
            if cache_key is not None:
                self._interval_cache[cache_key] = (
                    time.monotonic() + _INTERVAL_READ_CACHE_TTL_SECONDS,
                    interval,
                )
                if len(self._interval_cache) > _INTERVAL_READ_CACHE_MAX:
                    self._interval_cache.popitem(last=False)
            return interval

    def list_for_conversation(
        self, conversation_id: SnowflakeID, limit: int = 50
    ) -> List[RatchetInterval]:
        """Return intervals for a conversation, newest first."""
        with self._lock:
            if not self._table_exists(self._db):
                return []
            rows = self._db.fetch_all(
                """
                SELECT interval_id, conversation_id, start_message_id,
                       end_message_id, start_key_wrapped, created_at,
                       last_message_at
                FROM channel_ratchet_intervals
                WHERE conversation_id = ?
                ORDER BY start_message_id DESC
                LIMIT ?
                """,
                (conversation_id, int(limit)),
            )
            return [self._row_to_interval(r) for r in rows]

    def create(
        self,
        interval_id: SnowflakeID,
        conversation_id: SnowflakeID,
        start_message_id: SnowflakeID,
        start_key: bytes,
        now: int,
    ) -> RatchetInterval:
        """Insert a new open interval.

        Closes any currently-open interval for the same conversation
        atomically by writing ``end_message_id = start_message_id``
        before inserting the new row. Both writes happen under the
        store lock; the underlying ``db`` layer is expected to wrap
        them in a transaction.
        """
        with self._lock:
            if not self._table_exists(self._db):
                raise RatchetIntervalNotFoundError(
                    "channel_ratchet_intervals table is missing; run migration 045"
                )

            active = self.get_active(conversation_id)
            if active is not None:
                self._close_active(active, start_message_id, now)

            wrapped = self._wrap(start_key)
            self._db.execute(
                """
                INSERT INTO channel_ratchet_intervals
                    (interval_id, conversation_id, start_message_id,
                     end_message_id, start_key_wrapped, created_at,
                     last_message_at)
                VALUES (?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    interval_id,
                    conversation_id,
                    start_message_id,
                    wrapped,
                    now,
                    now,
                ),
            )
            return RatchetInterval(
                interval_id=interval_id,
                conversation_id=conversation_id,
                start_message_id=start_message_id,
                end_message_id=None,
                start_key=start_key,
                created_at=now,
                last_message_at=now,
            )

    def close_active(
        self,
        conversation_id: SnowflakeID,
        end_message_id: SnowflakeID,
        now: int,
    ) -> Optional[RatchetInterval]:
        """Mark the current open interval as closed.

        Returns the closed interval, or None if there was no open
        interval for ``conversation_id``.
        """
        with self._lock:
            active = self.get_active(conversation_id)
            if active is None:
                return None
            return self._close_active(active, end_message_id, now)

    def touch(self, interval_id: SnowflakeID, now: int) -> None:
        """Bump ``last_message_at`` on the interval."""
        with self._lock:
            self._invalidate_interval_cache(interval_id)
            self._db.execute(
                """
                UPDATE channel_ratchet_intervals
                SET last_message_at = ?
                WHERE interval_id = ?
                """,
                (now, interval_id),
            )

    def count_messages(self, interval_id: SnowflakeID) -> int:
        """Return how many messages reference this interval.

        Used by the rotation logic to decide whether the message-count
        threshold has been reached.
        """
        with self._lock:
            row = self._db.fetch_one(
                """
                SELECT COUNT(*) AS c
                FROM msg_messages
                WHERE ratchet_interval_id = ? AND deleted = 0
                """,
                (interval_id,),
            )
            if not row:
                return 0
            if isinstance(row, dict):
                return int(row.get("c") or 0)
            return int(row[0])

    def _close_active(
        self,
        active: RatchetInterval,
        end_message_id: SnowflakeID,
        now: int,
    ) -> RatchetInterval:
        self._invalidate_interval_cache(active.interval_id)
        self._db.execute(
            """
            UPDATE channel_ratchet_intervals
            SET end_message_id = ?, last_message_at = ?
            WHERE interval_id = ? AND end_message_id IS NULL
            """,
            (end_message_id, now, active.interval_id),
        )
        return RatchetInterval(
            interval_id=active.interval_id,
            conversation_id=active.conversation_id,
            start_message_id=active.start_message_id,
            end_message_id=end_message_id,
            start_key=active.start_key,
            created_at=active.created_at,
            last_message_at=now,
        )

    def _row_to_interval(self, row) -> RatchetInterval:
        if isinstance(row, dict):
            interval_id = row["interval_id"]
            conversation_id = row["conversation_id"]
            start_message_id = row["start_message_id"]
            end_message_id = row.get("end_message_id")
            wrapped = row.get("start_key_wrapped")
            created_at = row.get("created_at", 0)
            last_message_at = row.get("last_message_at", 0)
        else:
            interval_id = row[0]
            conversation_id = row[1]
            start_message_id = row[2]
            end_message_id = row[3]
            wrapped = row[4]
            created_at = row[5] if len(row) > 5 else 0
            last_message_at = row[6] if len(row) > 6 else 0

        raw_key: bytes = b""
        if wrapped:
            try:
                raw_key = self._unwrap(wrapped)
            except RatchetKeyWrapError:
                raw_key = b""

        return RatchetInterval(
            interval_id=interval_id,
            conversation_id=conversation_id,
            start_message_id=start_message_id,
            end_message_id=end_message_id,
            start_key=raw_key,
            created_at=int(created_at or 0),
            last_message_at=int(last_message_at or 0),
        )


__all__ = ["ChannelRatchetStore", "RatchetInterval"]

# ``json`` is imported above for potential future schema-only writers
# (for example, a CLI dump of the intervals table). Keep the import so
# static analysers do not flag it as unused after future edits.
_ = json
