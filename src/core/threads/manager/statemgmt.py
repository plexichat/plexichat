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
            if not self._is_member(user_id, thread_id):
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
            name_encrypted = None
            if name and self._encrypt_thread_names:
                from src.utils.encryption import encrypt_data

                name_encrypted = encrypt_data(name)

            updates.append("name = ?")
            params.append(name)

            if name_encrypted:
                updates.append("name_encrypted = ?")
                params.append(name_encrypted)

        if auto_archive_duration is not None:
            updates.append("auto_archive_duration = ?")
            params.append(auto_archive_duration.value)

        if updates:
            params.append(thread_id)
            allowed_columns = {"name", "auto_archive_duration"}
            for update in updates:
                col_name = update.split(" = ")[0]
                if col_name not in allowed_columns:
                    raise ValueError(f"Invalid column name: {col_name}")

            for update in updates:
                if "name = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE thread_threads SET name = ? WHERE id = ?",
                        (params[idx], thread_id),
                    )
                elif "auto_archive_duration = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE thread_threads SET auto_archive_duration = ? WHERE id = ?",
                        (params[idx], thread_id),
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
