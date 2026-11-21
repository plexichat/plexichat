from typing import Any, Dict, Optional, List

from src.core.security.hardening import bounded_int

from ..models import Thread, ThreadType, ThreadState
from ..exceptions import (
    ChannelNotFoundError,
)

from .helpers import (
    _get_channel,
    _get_thread_internal,
    _row_to_thread,
    _check_auto_archive,
)
from .protocol import ThreadProtocol

# Page bound enforced at the manager boundary. The HTTP route clamps its own
# query parameter, but ``get_archived_threads`` is also a public core read API
# (see ``src.core.threads.get_archived_threads``) called directly by internal
# callers and tests that bypass the route. The value is bound straight into
# ``LIMIT ?``; SQLite reads a negative LIMIT as *unlimited*, so an unclamped
# ``-1`` turned one archived-thread page into a full-table read. Bound every
# caller here, at the shared choke point, so the page contract cannot be
# sidestepped (non-integer / non-finite values are rejected outright).
_MAX_ARCHIVED_THREAD_PAGE = 200


class ThreadListingMixin(ThreadProtocol):
    def _channel_view_allowed(
        self, user_id: int, channel: Dict[str, Any], channel_id: int
    ) -> bool:
        """Evaluate ``channels.view`` for a channel, independent of its contents.

        SECURITY: the channel listing used to derive this from the threads it
        had already fetched, so a channel with no matching threads returned an
        empty list *before* the permission was ever evaluated -- the check was
        skipped rather than answered. The permission is a property of the
        caller and the channel, not of the rows, so it is resolved here, up
        front, and fails closed when the channel has no owning server or the
        servers module is not wired (``_check_permission``).
        """
        server_id = channel.get("server_id")
        if server_id is None:
            return False
        return self._check_permission(user_id, server_id, "channels.view", channel_id)

    def get_thread(self, user_id: int, thread_id: int) -> Optional[Thread]:
        thread = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        if not thread:
            return None

        # Reuse the row just loaded: ``can_view_thread(user_id, thread_id)``
        # would re-read this same ``thread_threads`` row via
        # ``_get_thread_internal`` before making the identical decision.
        if not self._can_view_thread_row(user_id, thread):
            return None

        now = self._get_timestamp()
        if _check_auto_archive(thread, now):
            self._db.execute(
                "UPDATE thread_threads SET state = ?, archived_at = ? WHERE id = ?",
                (ThreadState.ARCHIVED.value, now, thread_id),
            )
            return _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)

        return thread

    def get_active_threads(self, user_id: int, channel_id: int) -> List[Thread]:
        channel = _get_channel(self._db, channel_id)
        if not channel:
            raise ChannelNotFoundError(f"Channel {channel_id} not found")

        has_server_view = self._channel_view_allowed(user_id, channel, channel_id)

        # One index-backed query replaces the thread scan plus a separate
        # ``thread_members`` probe: the LEFT JOIN annotates each row with the
        # caller's membership marker, so private-thread visibility stays
        # membership-gated without a second round trip.
        rows = self._db.fetch_all(
            """SELECT t.*, m.user_id AS membership_user_id
               FROM thread_threads t
               LEFT JOIN thread_members m
                 ON m.thread_id = t.id AND m.user_id = ?
               WHERE t.channel_id = ? AND t.state = ? AND t.deleted = 0
               ORDER BY t.last_message_at DESC NULLS LAST""",
            (user_id, channel_id, ThreadState.ACTIVE.value),
        )

        threads = []
        member_thread_ids = {
            row["id"] for row in rows if row.get("membership_user_id") is not None
        }
        possible_threads = [
            _row_to_thread(row, self._encrypt_thread_names) for row in rows
        ]

        now = self._get_timestamp()
        active_threads = []
        auto_archived_ids = []
        for thread in possible_threads:
            if _check_auto_archive(thread, now):
                auto_archived_ids.append(thread.id)
                continue
            active_threads.append(thread)
        # One UPDATE for the whole expired batch instead of one per row. All
        # rows share the same ``now``, so the effect is identical while a
        # backlog of stale threads no longer issues a write per thread.
        if auto_archived_ids:
            placeholders = ",".join("?" * len(auto_archived_ids))
            self._db.execute(
                "UPDATE thread_threads SET state = ?, archived_at = ? "
                f"WHERE id IN ({placeholders})",
                (ThreadState.ARCHIVED.value, now, *auto_archived_ids),
            )

        if not active_threads:
            return []

        # SECURITY: ``has_server_view`` was resolved above from the channel
        # itself, so an empty listing cannot bypass the check. Public threads
        # are revealed only by that positive result.
        for thread in active_threads:
            if thread.thread_type == ThreadType.PRIVATE:
                # The JOIN proves membership, but a ``thread_members`` row
                # survives a server departure. Mirror ``can_view_thread``'s
                # private branch and require a live ``srv_members`` row too,
                # so a former member cannot keep enumerating the thread.
                if thread.id in member_thread_ids and self._is_server_member(
                    user_id, thread.server_id
                ):
                    threads.append(thread)
            elif has_server_view:
                threads.append(thread)

        return threads

    def get_archived_threads(
        self,
        user_id: int,
        channel_id: int,
        limit: int = 50,
        before_timestamp: Optional[int] = None,
    ) -> List[Thread]:
        # Bound the page size before it reaches ``LIMIT ?``. A negative limit
        # is *unlimited* in SQLite; validate at this boundary because direct
        # callers bypass the HTTP route's ``ge=1`` constraint.
        limit = bounded_int(
            limit, "limit", minimum=0, maximum=_MAX_ARCHIVED_THREAD_PAGE
        )

        channel = _get_channel(self._db, channel_id)
        if not channel:
            raise ChannelNotFoundError(f"Channel {channel_id} not found")

        has_server_view = self._channel_view_allowed(user_id, channel, channel_id)

        # Same single-query membership join as the active listing, with the
        # archived-at cursor folded into the index-backed query.
        _listing_sql = """SELECT t.*, m.user_id AS membership_user_id
                   FROM thread_threads t
                   LEFT JOIN thread_members m
                     ON m.thread_id = t.id AND m.user_id = ?
                   WHERE t.channel_id = ? AND t.state = ? AND t.deleted = 0 {archived_clause}
                   ORDER BY t.archived_at DESC, t.id DESC LIMIT ?"""
        if before_timestamp:
            rows = self._db.fetch_all(
                _listing_sql.format(archived_clause="AND t.archived_at < ?"),
                (
                    user_id,
                    channel_id,
                    ThreadState.ARCHIVED.value,
                    before_timestamp,
                    limit,
                ),
            )
        else:
            rows = self._db.fetch_all(
                _listing_sql.format(archived_clause=""),
                (user_id, channel_id, ThreadState.ARCHIVED.value, limit),
            )

        member_thread_ids = {
            row["id"] for row in rows if row.get("membership_user_id") is not None
        }
        archived_threads = [
            _row_to_thread(row, self._encrypt_thread_names) for row in rows
        ]
        if not archived_threads:
            return []

        # SECURITY: same up-front, contents-independent check as
        # get_active_threads (and the same fail-closed default).
        threads = []
        for thread in archived_threads:
            if thread.thread_type == ThreadType.PRIVATE:
                # Same live-roster gate as ``get_active_threads``: a stale
                # thread_members row must not keep a departed member in the
                # private-thread audience.
                if thread.id in member_thread_ids and self._is_server_member(
                    user_id, thread.server_id
                ):
                    threads.append(thread)
            elif has_server_view:
                threads.append(thread)

        return threads

    def get_user_threads(
        self, user_id: int, include_archived: bool = False
    ) -> List[Thread]:
        if include_archived:
            rows = self._db.fetch_all(
                """SELECT t.* FROM thread_threads t
                   JOIN thread_members m ON t.id = m.thread_id
                   WHERE m.user_id = ? AND t.deleted = 0
                   ORDER BY t.last_message_at DESC NULLS LAST""",
                (user_id,),
            )
        else:
            rows = self._db.fetch_all(
                """SELECT t.* FROM thread_threads t
                   JOIN thread_members m ON t.id = m.thread_id
                   WHERE m.user_id = ? AND t.state = ? AND t.deleted = 0
                   ORDER BY t.last_message_at DESC NULLS LAST""",
                (user_id, ThreadState.ACTIVE.value),
            )

        return self._filter_user_threads(rows, user_id)

    def get_user_private_threads(
        self, user_id: int, channel_id: Optional[int] = None
    ) -> List[Thread]:
        if channel_id:
            rows = self._db.fetch_all(
                """SELECT t.* FROM thread_threads t
                   JOIN thread_members m ON t.id = m.thread_id
                   WHERE m.user_id = ? AND t.thread_type = ? AND t.channel_id = ? AND t.deleted = 0
                   ORDER BY t.last_message_at DESC NULLS LAST""",
                (user_id, ThreadType.PRIVATE.value, channel_id),
            )
        else:
            rows = self._db.fetch_all(
                """SELECT t.* FROM thread_threads t
                   JOIN thread_members m ON t.id = m.thread_id
                   WHERE m.user_id = ? AND t.thread_type = ? AND t.deleted = 0
                   ORDER BY t.last_message_at DESC NULLS LAST""",
                (user_id, ThreadType.PRIVATE.value),
            )

        return self._filter_user_threads(rows, user_id)

    def _filter_user_threads(self, rows: List[Any], user_id: int) -> List[Thread]:
        """Re-check channel/server access for every listed thread.

        Membership alone is not proof of visibility: a user's channel or
        server access can be revoked after they joined a public thread.
        Private threads stay membership-gated by definition; public ones
        must still pass ``channels.view`` (which fails closed without a
        servers module).

        The listing query already returned every field needed for that check.
        Calling ``can_view_thread(user_id, thread.id)`` here used to re-read the
        exact same ``thread_threads`` row via ``_get_thread_internal`` -- one
        redundant point read per public thread (plus a wasted name decrypt).
        Evaluate the identical ``_check_permission`` call from the row in hand
        instead: same authorization decision, no per-row fetch.
        """
        visible: List[Thread] = []
        for row in rows:
            thread = _row_to_thread(row, self._encrypt_thread_names)
            if thread.thread_type == ThreadType.PRIVATE:
                # The listing JOIN proves membership, but leaving/kicking a
                # server deletes only ``srv_members`` -- the thread_members row
                # survives. A private thread therefore needs a LIVE backing-
                # server roster row here, exactly as ``can_view_thread``
                # requires. Without it, a former member keeps reading their
                # private threads (name/owner/counts) via ``/users/@me/threads``.
                if self._is_server_member(user_id, thread.server_id):
                    visible.append(thread)
            elif self._check_permission(
                user_id, thread.server_id, "channels.view", thread.channel_id
            ):
                visible.append(thread)
        return visible
