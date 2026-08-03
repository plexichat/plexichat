from typing import Optional

import utils.logger as logger
from ..models import Thread, ThreadState, AutoArchiveDuration
from ..exceptions import (
    ThreadNotFoundError,
    PermissionDeniedError,
)

from .helpers import _get_thread_internal
from .protocol import ThreadProtocol


class ThreadStateMixin(ThreadProtocol):
    def _unarchive_thread_internal(self, thread_id: int) -> None:
        self._db.execute(
            "UPDATE thread_threads SET state = ?, archived_at = NULL WHERE id = ?",
            (ThreadState.ACTIVE.value, thread_id),
        )

    def archive_thread(self, user_id: int, thread_id: int) -> Thread:
        thread = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        if not self.can_manage_thread(user_id, thread_id):
            raise PermissionDeniedError("Cannot archive thread", "threads.manage")

        if thread.state == ThreadState.ARCHIVED:
            return thread

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE thread_threads SET state = ?, archived_at = ? WHERE id = ?",
            (ThreadState.ARCHIVED.value, now, thread_id),
        )

        logger.debug(f"Thread {thread_id} archived by user {user_id}")

        result = self.get_thread(user_id, thread_id)
        assert result is not None
        return result

    def unarchive_thread(self, user_id: int, thread_id: int) -> Thread:
        thread = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        if not self.can_manage_thread(user_id, thread_id):
            raise PermissionDeniedError("Cannot unarchive thread", "threads.manage")

        if thread.state != ThreadState.ARCHIVED:
            return thread

        self._unarchive_thread_internal(thread_id)

        logger.debug(f"Thread {thread_id} unarchived by user {user_id}")

        result = self.get_thread(user_id, thread_id)
        assert result is not None
        return result

    def lock_thread(self, user_id: int, thread_id: int) -> Thread:
        thread = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        if not self.can_manage_thread(user_id, thread_id):
            raise PermissionDeniedError("Cannot lock thread", "threads.manage")

        self._db.execute(
            "UPDATE thread_threads SET locked = 1 WHERE id = ?", (thread_id,)
        )

        logger.debug(f"Thread {thread_id} locked by user {user_id}")

        result = self.get_thread(user_id, thread_id)
        assert result is not None
        return result

    def unlock_thread(self, user_id: int, thread_id: int) -> Thread:
        thread = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        if not self.can_manage_thread(user_id, thread_id):
            raise PermissionDeniedError("Cannot unlock thread", "threads.manage")

        self._db.execute(
            "UPDATE thread_threads SET locked = 0 WHERE id = ?", (thread_id,)
        )

        logger.debug(f"Thread {thread_id} unlocked by user {user_id}")

        result = self.get_thread(user_id, thread_id)
        assert result is not None
        return result

    def update_thread(
        self,
        user_id: int,
        thread_id: int,
        name: Optional[str] = None,
        auto_archive_duration: Optional[AutoArchiveDuration] = None,
    ) -> Thread:
        thread = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        if not self.can_manage_thread(user_id, thread_id):
            raise PermissionDeniedError("Cannot update thread", "threads.manage")

        updates = []
        params = []

        if name is not None:
            name = self._validate_thread_name(name)
            if self._encrypt_thread_names:
                from src.utils.encryption import encrypt_data

                updates.append("name = ?")
                params.append("[encrypted]")
                updates.append("name_encrypted = ?")
                params.append(encrypt_data(name))
            else:
                updates.append("name = ?")
                params.append(name)
                updates.append("name_encrypted = ?")
                params.append(None)

        if auto_archive_duration is not None:
            updates.append("auto_archive_duration = ?")
            params.append(auto_archive_duration.value)

        if updates:
            params.append(thread_id)
            self._db.execute(
                f"UPDATE thread_threads SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )

        logger.debug(f"Thread {thread_id} updated by user {user_id}")

        result = self.get_thread(user_id, thread_id)
        assert result is not None
        return result

    def delete_thread(self, user_id: int, thread_id: int) -> bool:
        thread = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        if not self.can_manage_thread(user_id, thread_id):
            raise PermissionDeniedError("Cannot delete thread", "threads.manage")

        self._db.execute(
            "UPDATE thread_threads SET deleted = 1 WHERE id = ?", (thread_id,)
        )

        logger.debug(f"Thread {thread_id} deleted by user {user_id}")

        return True
