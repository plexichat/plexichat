from typing import Optional, List, Any

import utils.logger as logger
from ..models import ThreadMember, ThreadType
from ..exceptions import (
    ThreadNotFoundError,
    ThreadAccessDeniedError,
    ThreadMemberNotFoundError,
    ThreadMemberExistsError,
    PermissionDeniedError,
)

from .helpers import _get_thread_internal, _row_to_thread_member
from .protocol import ThreadProtocol

#: Hard ceiling for ``get_thread_members``. The ``limit`` argument used to be
#: clamped from below only (``max(0, limit)``); a large positive value reached
#: SQL ``LIMIT ?`` verbatim and returned the entire membership roster, turning
#: one request into an unbounded query/serialization. 100 matches this method's
#: default page size and the other thread/relationship listings.
MAX_MEMBERS_PAGE_SIZE = 100


class ThreadMembershipMixin(ThreadProtocol):
    def _is_member(self, user_id: int, thread_id: int) -> bool:
        row = self._db.fetch_one(
            "SELECT 1 FROM thread_members WHERE thread_id = ? AND user_id = ?",
            (thread_id, user_id),
        )
        return row is not None

    @staticmethod
    def _invalidate_thread_audience_caches() -> None:
        """Retire cached gateway audiences after a thread membership write.

        ``resolve_channel_event_recipients`` memoizes a channel's fully
        resolved recipient list in ``_channel_recipient_cache`` keyed by the
        *server's RBAC epoch*. A thread membership write (join / leave /
        add / remove) changes the exact audience of that thread's
        channel-scoped events (typing, reactions, read receipts, message
        activity) but does NOT bump the server epoch, so without an explicit
        flush the cached list is still keyed to the old membership: a member
        removed from a private thread stays in the warm recipient list for the
        whole TTL and keeps receiving events from a thread they no longer
        belong to. Evict on every membership write so no stale audience can be
        replayed, mirroring the server-level membership funnel
        (``invalidate_member_count_cache``). Lazy import keeps the member tier
        decoupled from the event layer at module load.
        """
        try:
            from src.core.events.gateway_emit import clear_channel_recipient_cache

            clear_channel_recipient_cache()
        except Exception:  # pragma: no cover - event layer optional
            pass

    def get_all_member_user_ids(self, thread_id: int) -> List[int]:
        """Internal-only: every user id that is a member of ``thread_id``.

        This helper deliberately has NO caller permission gate. It exists so
        trusted internal callers (gateway event routing) can scope a
        private-thread dispatch to the thread's exact audience instead of
        falling back to "all members of the server". It returns ids only —
        never the thread itself or its content. Do not expose through the API.
        """
        # Leaving/kicking a server deletes the ``srv_members`` row but does
        # NOT prune ``thread_members``. Intersect the stale thread roster with
        # the live server roster so a former member does not keep receiving
        # private thread events. Resolve the backing server WITHOUT the
        # ``deleted = 0`` filter (delete events are emitted post-tombstone)
        # and fail closed when it cannot be resolved.
        server_row = self._db.fetch_one(
            "SELECT server_id FROM thread_threads WHERE id = ?", (thread_id,)
        )
        if not server_row or server_row.get("server_id") is None:
            return []
        server_id = server_row["server_id"]

        rows = self._db.fetch_all(
            """SELECT tm.user_id FROM thread_members tm
               JOIN srv_members sm
                 ON sm.user_id = tm.user_id AND sm.server_id = ?
               WHERE tm.thread_id = ?""",
            (server_id, thread_id),
        )
        # SECURITY: a joined thread_members/srv_members.user_id vouches for a
        # live member of the private thread's audience only while it is a
        # genuine integer id. Coercing a malformed value with int() (e.g.
        # "05"->5, True->1, 5.9->5) would put a user in the gateway event
        # audience whose membership was never actually proven, leaking private
        # thread name/owner/existence and message/typing activity. Fail closed:
        # only exact int ids from the authoritative roster may enter.
        return [
            uid for uid in (row["user_id"] for row in rows or []) if type(uid) is int
        ]

    def get_thread_type_value(self, thread_id: int) -> Optional[Any]:
        """Internal-only: the ``thread_type`` value of a thread row.

        Unlike ``_get_thread_internal`` this does NOT filter on ``deleted = 0``:
        delete events are emitted after the tombstone is written, so routing
        still needs to know whether the (now deleted) thread was private.
        Returns ``None`` for an unknown id. Do not expose through the API.
        """
        row = self._db.fetch_one(
            "SELECT thread_type FROM thread_threads WHERE id = ?",
            (thread_id,),
        )
        if not row:
            return None
        raw = row["thread_type"]
        try:
            return int(raw)
        except (TypeError, ValueError):
            # ThreadType is a str-valued enum in this codebase; the caller
            # normalizes both int and string forms.
            return raw

    def _update_member_count(self, thread_id: int) -> None:
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM thread_members WHERE thread_id = ?",
            (thread_id,),
        )
        count = row["count"] if row else 0
        self._db.execute(
            "UPDATE thread_threads SET member_count = ? WHERE id = ?",
            (count, thread_id),
        )

    def _get_member(self, thread_id: int, user_id: int) -> Optional[ThreadMember]:
        row = self._db.fetch_one(
            "SELECT * FROM thread_members WHERE thread_id = ? AND user_id = ?",
            (thread_id, user_id),
        )
        return _row_to_thread_member(row) if row else None

    def join_thread(self, user_id: int, thread_id: int) -> ThreadMember:
        thread = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        if not self.can_view_thread(user_id, thread_id):
            raise ThreadAccessDeniedError("Cannot access this thread")

        if self._is_member(user_id, thread_id):
            raise ThreadMemberExistsError("Already a member of this thread")

        now = self._get_timestamp()
        self._db.execute(
            """INSERT INTO thread_members (thread_id, user_id, joined_at)
               VALUES (?, ?, ?)""",
            (thread_id, user_id, now),
        )

        self._update_member_count(thread_id)
        self._invalidate_thread_audience_caches()

        logger.debug(f"User {user_id} joined thread {thread_id}")

        member = self._get_member(thread_id, user_id)
        assert member is not None
        return member

    def leave_thread(self, user_id: int, thread_id: int) -> bool:
        thread = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        if not self._is_member(user_id, thread_id):
            raise ThreadMemberNotFoundError("Not a member of this thread")

        if user_id == thread.owner_id:
            raise PermissionDeniedError(
                "Thread owner cannot leave their own thread", "threads.manage"
            )

        self._db.execute(
            "DELETE FROM thread_members WHERE thread_id = ? AND user_id = ?",
            (thread_id, user_id),
        )

        self._update_member_count(thread_id)
        self._invalidate_thread_audience_caches()

        logger.debug(f"User {user_id} left thread {thread_id}")

        return True

    def _validate_add_target(self, thread, member_user_id: int) -> None:
        """Validate a prospective thread member BEFORE any insert.

        The target must be an existing user (auth lookup when available)
        AND a member of the thread's backing server; for public threads
        they must additionally be able to view the parent channel.
        Violations raise 404/403-mapped domain errors instead of silently
        creating orphaned memberships.
        """
        # Unknown users are rejected outright (404 semantics).
        if self._auth:
            try:
                target = self._auth.get_user(member_user_id)
            except Exception:
                target = None
            if not target:
                raise ThreadMemberNotFoundError(f"User {member_user_id} does not exist")

        # Must belong to the server that owns the thread's channel.
        row = self._db.fetch_one(
            "SELECT 1 FROM srv_members WHERE server_id = ? AND user_id = ?",
            (thread.server_id, member_user_id),
        )
        if not row:
            raise ThreadAccessDeniedError("User is not a member of the backing server")

        # Public threads: the target must actually be able to see the
        # parent channel (fail-closed permission check).
        if thread.thread_type != ThreadType.PRIVATE and not self.can_view_thread(
            member_user_id, thread.id
        ):
            raise ThreadAccessDeniedError("User cannot view this thread's channel")

    def add_member(
        self, user_id: int, thread_id: int, member_user_id: int
    ) -> ThreadMember:
        thread = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        # Membership changes must not reach a thread the caller can no longer
        # see. A public-thread member keeps their ``thread_members`` row after
        # a channel override revokes ``channels.view``; the bare membership
        # check below would otherwise let them add people to -- and hand
        # access to -- a now-hidden thread. Reuse the row in hand and fail
        # closed. (Private threads are additionally gated by
        # ``can_manage_thread`` below, which now enforces this too.)
        if not self._can_view_thread_row(user_id, thread):
            raise ThreadAccessDeniedError("Cannot access this thread")

        if not self._is_member(user_id, thread_id):
            raise ThreadAccessDeniedError("Must be a member to add others")

        if self._is_member(member_user_id, thread_id):
            raise ThreadMemberExistsError("User is already a member")

        if thread.thread_type == ThreadType.PRIVATE:
            if not self.can_manage_thread(user_id, thread_id):
                raise PermissionDeniedError(
                    "Cannot add members to private thread", "threads.manage"
                )

        self._validate_add_target(thread, member_user_id)

        now = self._get_timestamp()
        self._db.execute(
            """INSERT INTO thread_members (thread_id, user_id, joined_at)
               VALUES (?, ?, ?)""",
            (thread_id, member_user_id, now),
        )

        self._update_member_count(thread_id)
        self._invalidate_thread_audience_caches()

        logger.debug(f"User {member_user_id} added to thread {thread_id} by {user_id}")

        member = self._get_member(thread_id, member_user_id)
        assert member is not None
        return member

    def remove_member(self, user_id: int, thread_id: int, member_user_id: int) -> bool:
        thread = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        if not self._is_member(member_user_id, thread_id):
            raise ThreadMemberNotFoundError("User is not a member")

        if member_user_id == thread.owner_id:
            raise PermissionDeniedError("Cannot remove thread owner", "threads.manage")

        if user_id != member_user_id:
            # Private-thread audiences are invite-only and invisible to
            # non-members. A channel-level ``threads.manage`` grant must not
            # let someone who is not in the thread's audience strip its
            # roster: ``add_member`` applies the same caller-membership rule.
            # Check membership first so a non-member gets the same denial
            # regardless of whether they happen to hold the manage grant.
            if thread.thread_type == ThreadType.PRIVATE and not self._is_member(
                user_id, thread_id
            ):
                raise ThreadAccessDeniedError(
                    "Must be a member to manage a private thread's members"
                )
            if not self.can_manage_thread(user_id, thread_id):
                raise PermissionDeniedError("Cannot remove members", "threads.manage")

        self._db.execute(
            "DELETE FROM thread_members WHERE thread_id = ? AND user_id = ?",
            (thread_id, member_user_id),
        )

        self._update_member_count(thread_id)
        self._invalidate_thread_audience_caches()

        logger.debug(
            f"User {member_user_id} removed from thread {thread_id} by {user_id}"
        )

        return True

    def get_thread_members(
        self,
        user_id: int,
        thread_id: int,
        limit: int = 100,
        after_user_id: Optional[int] = None,
    ) -> List[ThreadMember]:
        if not self.can_view_thread(user_id, thread_id):
            raise ThreadAccessDeniedError("Cannot access this thread")

        # Clamp from both sides: SQLite treats ``LIMIT -1`` as *unlimited*, so a
        # negative limit would dump the entire membership roster, while an
        # oversized positive limit (e.g. 100000000) would return every member
        # and blow up the query/serialization. Non-integer input fails closed
        # to an empty page. Mirrors the clamp in get_messages() and the
        # relationship/blocking listings.
        try:
            limit = int(limit)
        except (TypeError, ValueError, OverflowError):
            limit = 0
        limit = max(0, min(limit, MAX_MEMBERS_PAGE_SIZE))

        if after_user_id:
            rows = self._db.fetch_all(
                """SELECT * FROM thread_members 
                   WHERE thread_id = ? AND user_id > ?
                   ORDER BY user_id LIMIT ?""",
                (thread_id, after_user_id, limit),
            )
        else:
            rows = self._db.fetch_all(
                """SELECT * FROM thread_members 
                   WHERE thread_id = ?
                   ORDER BY user_id LIMIT ?""",
                (thread_id, limit),
            )

        return [_row_to_thread_member(row) for row in rows]
