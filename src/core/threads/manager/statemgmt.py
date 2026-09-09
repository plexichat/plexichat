from typing import List, Optional

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
        # Bump last_message_at to NOW in the same UPDATE that clears the
        # archived state: the auto-archive window is measured from
        # last_activity, so an explicitly unarchived (stale) thread would
        # otherwise be instantly re-archived by _check_auto_archive on the
        # very next read.
        # TOCTOU guard (M5): only flip ARCHIVED -> ACTIVE so a concurrent
        # archive/unarchive race resolves to exactly one winner instead of
        # last-writer-wins on stale state. A lost race is a harmless no-op;
        # callers re-read afterwards.
        self._db.execute(
            "UPDATE thread_threads SET state = ?, archived_at = NULL, "
            "last_message_at = ? WHERE id = ? AND state = ?",
            (
                ThreadState.ACTIVE.value,
                self._get_timestamp(),
                thread_id,
                ThreadState.ARCHIVED.value,
            ),
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
        # TOCTOU guard (M5): only archive a non-archived thread. A
        # concurrent archiver that commits first makes this UPDATE match
        # zero rows; treat that as already-in-desired-state (re-read and
        # return, fail closed when the thread is gone) instead of blindly
        # overwriting archived_at on stale state.
        cursor = self._db.execute(
            "UPDATE thread_threads SET state = ?, archived_at = ? "
            "WHERE id = ? AND state != ?",
            (
                ThreadState.ARCHIVED.value,
                now,
                thread_id,
                ThreadState.ARCHIVED.value,
            ),
        )
        if getattr(cursor, "rowcount", 1) == 0:
            current = _get_thread_internal(
                self._db, thread_id, self._encrypt_thread_names
            )
            if current is None:
                raise ThreadNotFoundError(f"Thread {thread_id} not found")
            return current

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

        # Re-read after the conditional UPDATE: a lost concurrent race
        # leaves the row untouched, so return the current state (no-op
        # when already ACTIVE); fail closed when the thread is gone.
        current = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        if current is None:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

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

        # Invalidate the slowmode cache so a deleted thread's interval is
        # never served from cache post-delete.
        try:
            from src.core.database import cache_delete

            cache_delete(f"thread_slowmode:{thread_id}")
        except Exception as e:
            logger.debug(f"Failed to invalidate slowmode cache for {thread_id}: {e}")

        logger.debug(f"Thread {thread_id} deleted by user {user_id}")

        return True

    def delete_threads_for_channel(self, channel_id: int) -> List[int]:
        """Atomically cascade-delete every thread of a channel.

        Runs the full cleanup (thread_messages links, thread_members rows,
        ``deleted = 1`` tombstones and ``parent_message_id`` unlinking)
        inside ONE transaction so a mid-cascade failure leaves every
        thread intact instead of orphaning half-deleted rows.

        Returns:
            The ids of the threads that were deleted.
        """
        rows = self._db.fetch_all(
            "SELECT id FROM thread_threads WHERE channel_id = ? AND deleted = 0",
            (channel_id,),
        )
        thread_ids = [int(row["id"]) for row in rows]
        if not thread_ids:
            return []

        placeholders = ",".join("?" * len(thread_ids))
        self._db.begin_transaction()
        try:
            self._db.execute(
                f"DELETE FROM thread_messages WHERE thread_id IN ({placeholders})",
                tuple(thread_ids),
            )
            self._db.execute(
                f"DELETE FROM thread_members WHERE thread_id IN ({placeholders})",
                tuple(thread_ids),
            )
            self._db.execute(
                f"UPDATE thread_threads SET deleted = 1, parent_message_id = NULL "
                f"WHERE id IN ({placeholders})",
                tuple(thread_ids),
            )
            self._db.commit()
        except Exception:
            try:
                self._db.rollback()
            except Exception as rollback_exc:
                logger.error(
                    f"Failed to rollback thread cascade delete for channel "
                    f"{channel_id}: {rollback_exc}",
                )
                raise
            raise
        finally:
            # Post-delete (or post-failure) cache cleanup: never serve a
            # deleted thread's slowmode from cache.
            if thread_ids:
                try:
                    from src.core.database import cache_delete

                    for tid in thread_ids:
                        cache_delete(f"thread_slowmode:{tid}")
                except Exception as e:
                    logger.debug(
                        f"Failed to invalidate slowmode caches after cascade "
                        f"delete of channel {channel_id}: {e}"
                    )

        logger.debug(
            f"Cascade-deleted {len(thread_ids)} thread(s) for channel {channel_id}"
        )
        return thread_ids
