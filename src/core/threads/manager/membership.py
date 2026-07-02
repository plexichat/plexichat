from typing import Optional, List

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


class ThreadMembershipMixin(ThreadProtocol):
    def _is_member(self, user_id: int, thread_id: int) -> bool:
        row = self._db.fetch_one(
            "SELECT 1 FROM thread_members WHERE thread_id = ? AND user_id = ?",
            (thread_id, user_id),
        )
        return row is not None

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

        self._db.execute(
            "DELETE FROM thread_members WHERE thread_id = ? AND user_id = ?",
            (thread_id, user_id),
        )

        self._update_member_count(thread_id)

        logger.debug(f"User {user_id} left thread {thread_id}")

        return True

    def add_member(
        self, user_id: int, thread_id: int, member_user_id: int
    ) -> ThreadMember:
        thread = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        if not self._is_member(user_id, thread_id):
            raise ThreadAccessDeniedError("Must be a member to add others")

        if self._is_member(member_user_id, thread_id):
            raise ThreadMemberExistsError("User is already a member")

        if thread.thread_type == ThreadType.PRIVATE:
            if not self.can_manage_thread(user_id, thread_id):
                raise PermissionDeniedError(
                    "Cannot add members to private thread", "threads.manage"
                )

        now = self._get_timestamp()
        self._db.execute(
            """INSERT INTO thread_members (thread_id, user_id, joined_at)
               VALUES (?, ?, ?)""",
            (thread_id, member_user_id, now),
        )

        self._update_member_count(thread_id)

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

        if user_id != member_user_id:
            if not self.can_manage_thread(user_id, thread_id):
                raise PermissionDeniedError("Cannot remove members", "threads.manage")

        if member_user_id == thread.owner_id and user_id != thread.owner_id:
            raise PermissionDeniedError("Cannot remove thread owner", "threads.manage")

        self._db.execute(
            "DELETE FROM thread_members WHERE thread_id = ? AND user_id = ?",
            (thread_id, member_user_id),
        )

        self._update_member_count(thread_id)

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
