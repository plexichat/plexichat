"""
Thread slowmode - Rate limiting for thread messages.

Provides per-thread slowmode that operates independently of the parent
channel's slowmode, allowing fine-grained control over message frequency
in individual threads.

Slowmode is enforced by checking the time elapsed since a user's last
message in the thread against the configured interval.
"""

import time
from typing import Dict, Any

import utils.logger as logger
from src.core.database import cache_get, cache_set


class ThreadSlowmode:
    """Manages slowmode settings and enforcement for threads."""

    # Slowmode limits
    MIN_INTERVAL_MS = 1000  # 1 second minimum
    MAX_INTERVAL_MS = 21600000  # 6 hours maximum
    DEFAULT_INTERVAL_MS = 0  # Disabled by default

    # Cache TTL for slowmode checks (slightly longer than max interval)
    CACHE_TTL = 21600  # 6 hours in seconds

    def __init__(self, db):
        self._db = db

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    def set_slowmode(
        self,
        thread_id: int,
        interval_ms: int,
        user_id: int,
        can_manage: bool = False,
    ) -> Dict[str, Any]:
        """
        Set slowmode interval for a thread.

        Args:
            thread_id: ID of the thread
            interval_ms: Minimum time between messages in milliseconds (0 to disable)
            user_id: ID of the user setting slowmode
            can_manage: Whether the user has manage permission (pre-checked)

        Returns:
            Updated slowmode settings dict

        Raises:
            PermissionError: If user cannot manage the thread
            ValueError: If interval is out of range
        """
        if not can_manage:
            raise PermissionError("Missing permission to manage thread slowmode")

        # Validate interval
        if interval_ms != 0:
            if interval_ms < self.MIN_INTERVAL_MS:
                raise ValueError(
                    f"Slowmode interval must be at least {self.MIN_INTERVAL_MS}ms"
                )
            if interval_ms > self.MAX_INTERVAL_MS:
                raise ValueError(
                    f"Slowmode interval cannot exceed {self.MAX_INTERVAL_MS}ms "
                    f"({self.MAX_INTERVAL_MS // 3600000} hours)"
                )

        # Verify thread exists
        thread_row = self._db.fetch_one(
            "SELECT id FROM thread_threads WHERE id = ?",
            (thread_id,),
        )
        if not thread_row:
            raise ValueError("Thread not found")

        now = self._get_timestamp()

        # Update the thread's slowmode columns
        self._db.execute(
            """UPDATE thread_threads
               SET slowmode_interval_ms = ?, slowmode_updated_by = ?, slowmode_updated_at = ?
               WHERE id = ?""",
            (interval_ms, user_id, now, thread_id),
        )

        # Invalidate cache
        cache_key = f"thread_slowmode:{thread_id}"
        try:
            from src.core.database import cache_delete

            cache_delete(cache_key)
        except Exception:
            pass

        logger.debug(
            f"Thread {thread_id} slowmode set to {interval_ms}ms by user {user_id}"
        )

        return {
            "thread_id": thread_id,
            "interval_ms": interval_ms,
            "updated_by": user_id,
            "updated_at": now,
        }

    def get_slowmode(self, thread_id: int) -> Dict[str, Any]:
        """Get slowmode settings for a thread."""
        # Try cache first
        cache_key = f"thread_slowmode:{thread_id}"
        cached = cache_get(cache_key)
        if cached:
            return cached

        row = self._db.fetch_one(
            """SELECT slowmode_interval_ms, slowmode_updated_by, slowmode_updated_at
               FROM thread_threads WHERE id = ?""",
            (thread_id,),
        )

        result = {
            "thread_id": thread_id,
            "interval_ms": row["slowmode_interval_ms"]
            if row and row["slowmode_interval_ms"]
            else 0,
            "updated_by": row["slowmode_updated_by"] if row else None,
            "updated_at": row["slowmode_updated_at"] if row else None,
        }

        # Cache it
        cache_set(cache_key, result, ttl=self.CACHE_TTL)

        return result

    def check_slowmode(
        self, thread_id: int, user_id: int, is_moderator: bool = False
    ) -> Dict[str, Any]:
        """
        Check if a user can send a message in a thread with slowmode.

        Moderators and users with manage permission are exempt from slowmode.

        Args:
            thread_id: ID of the thread
            user_id: ID of the user sending the message
            is_moderator: Whether the user is exempt from slowmode

        Returns:
            Dict with 'allowed' (bool) and 'retry_after_ms' (int, 0 if allowed)
        """
        if is_moderator:
            return {"allowed": True, "retry_after_ms": 0}

        settings = self.get_slowmode(thread_id)
        interval_ms = settings["interval_ms"]

        if interval_ms == 0:
            return {"allowed": True, "retry_after_ms": 0}

        # Check last message time in this thread
        now = self._get_timestamp()

        # Check cache for last message time
        last_msg_key = f"thread_last_msg:{thread_id}:{user_id}"
        last_msg_time = cache_get(last_msg_key)

        if last_msg_time is None:
            # Fall back to database query
            row = self._db.fetch_one(
                """SELECT MAX(tm.created_at) as last_msg_at
                   FROM thread_messages tm
                   WHERE tm.thread_id = ? AND tm.user_id = ?""",
                (thread_id, user_id),
            )
            last_msg_time = row["last_msg_at"] if row and row["last_msg_at"] else 0

        elapsed = now - last_msg_time
        remaining = interval_ms - elapsed

        if remaining <= 0:
            # User can send - update last message time in cache
            cache_set(last_msg_key, now, ttl=self.CACHE_TTL)
            return {"allowed": True, "retry_after_ms": 0}

        return {"allowed": False, "retry_after_ms": remaining}

    def record_message_sent(self, thread_id: int, user_id: int) -> None:
        """
        Record that a message was sent in a thread for slowmode tracking.

        Called after a message is successfully sent to update the
        last-message timestamp cache.
        """
        now = self._get_timestamp()
        last_msg_key = f"thread_last_msg:{thread_id}:{user_id}"
        cache_set(last_msg_key, now, ttl=self.CACHE_TTL)
