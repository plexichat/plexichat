"""Status operations mixin.

Handles setting, getting, and clearing user online status
with Valkey caching for high-speed lookups.
"""

from typing import List, Union

import utils.logger as logger
from src.core.database import (
    cache_set,
    valkey_available,
    get_valkey_client,
)

from .base import PresenceManagerBase
from ..models import Presence, UserStatus
from ..exceptions import InvalidStatusError


class StatusMixin(PresenceManagerBase):
    """Mixin providing online status operations."""

    # Timestamp (ms) of the last ghost-entry revalidation of the Valkey
    # online-users set. Class-level so it throttles once per process.
    _online_set_revalidated_at: int = 0

    def _update_valkey_presence(self, user_id: int, status: str) -> None:
        """Update online users set in Valkey for high-speed lookups."""
        if not valkey_available():
            return

        client = get_valkey_client()
        if not client:
            return

        try:
            key = "presence:online_users"
            if status in ("online", "idle", "dnd"):
                client.sadd(key, str(user_id))
                # CORRECTNESS FIX: without a TTL the online-users set grows
                # unbounded and retains stale entries after a server restart
                # or a dropped connection that never sent OFFLINE. Refresh a
                # bounded TTL on every add so the index self-heals.
                client.expire(key, self._presence_timeout_ms // 1000)
            else:
                client.srem(key, str(user_id))
        except Exception as e:
            logger.debug(f"Failed to update online users set: {e}")

    def _revalidate_online_users_set(self) -> None:
        """Periodically evict stale members from the Valkey online-users set.

        CORRECTNESS FIX: the whole-set TTL is refreshed whenever ANY member
        heartbeats, so stale member IDs could survive indefinitely as long as
        one user stayed online and every reader trusted the set exclusively.
        Piggybacked on the heartbeat/update path, this revalidates members at
        most once per minute against the persisted presence rows: a member is
        only kept when its status is live AND its last_seen is within the
        presence timeout. Everything else is SREM'd.
        """
        now = self._get_timestamp()
        if now - StatusMixin._online_set_revalidated_at < 60000:
            return

        if not valkey_available():
            return

        client = get_valkey_client()
        if not client:
            return

        StatusMixin._online_set_revalidated_at = now

        try:
            members = client.smembers("presence:online_users")
            if not members:
                return

            candidate_ids: List[int] = []
            for raw in members:
                try:
                    candidate_ids.append(int(raw))
                except (TypeError, ValueError):
                    continue
            if not candidate_ids:
                return

            cutoff = now - self._presence_timeout_ms
            valid_ids: set = set()
            chunk_size = 500
            for start in range(0, len(candidate_ids), chunk_size):
                chunk = candidate_ids[start : start + chunk_size]
                placeholders = ",".join("?" * len(chunk))
                rows = self._db.fetch_all(
                    f"""SELECT user_id FROM pres_presence
                        WHERE user_id IN ({placeholders})
                        AND status IN ('online', 'idle', 'dnd')
                        AND last_seen >= ?""",
                    tuple(chunk) + (cutoff,),
                )
                valid_ids.update(row["user_id"] for row in rows)

            stale = [uid for uid in candidate_ids if uid not in valid_ids]
            if stale:
                client.srem("presence:online_users", *[str(uid) for uid in stale])
                logger.debug(
                    f"Revalidated online-users set; evicted {len(stale)} stale members"
                )
        except Exception as e:
            logger.debug(f"Online-users set revalidation failed: {e}")

    def sweep_all_offline(self) -> int:
        """Mark every persisted presence row offline (startup crash recovery).

        Runs under a short-lived distributed lock so multi-worker deployments
        don't have every worker sweeping (and racing each other's presence
        writes / the Valkey index) simultaneously. Exactly one worker performs
        the sweep; on single-node deployments the lock is free and the ghost
        cleanup still happens. Returns the number of rows transitioned out of
        a live status (0 when another worker holds the lock).
        """
        lock_key = "presence:startup_sweep"
        client = get_valkey_client() if valkey_available() else None
        lock_token = None

        if client is not None:
            acquirer = getattr(client, "acquire_lock", None)
            if callable(acquirer):
                try:
                    lock_token = acquirer(lock_key, timeout=5.0, lock_timeout=15000)
                except Exception as e:
                    logger.debug(f"Presence sweep lock acquisition failed: {e}")
                    lock_token = None
                if lock_token is None:
                    logger.info(
                        "Presence startup sweep skipped: another worker is sweeping"
                    )
                    return 0

        try:
            return self._sweep_all_offline_locked()
        finally:
            if client is not None and lock_token:
                releaser = getattr(client, "release_lock", None)
                if callable(releaser):
                    try:
                        releaser(lock_key, lock_token)
                    except Exception as e:
                        logger.debug(f"Presence sweep lock release failed: {e}")

    def _sweep_all_offline_locked(self) -> int:
        """Lock-held body of :meth:`sweep_all_offline`."""
        count = 0
        try:
            result = self._db.execute(
                "UPDATE pres_presence SET status = ?, updated_at = ? WHERE status != ?",
                (
                    UserStatus.OFFLINE.value,
                    self._get_timestamp(),
                    UserStatus.OFFLINE.value,
                ),
            )
            count = getattr(result, "rowcount", 0) or 0
        except Exception as e:
            logger.warning(f"Presence startup sweep DB update failed: {e}")

        if valkey_available():
            client = get_valkey_client()
            if client:
                try:
                    client.delete("presence:online_users")
                except Exception as e:
                    logger.debug(f"Presence startup sweep cache clear failed: {e}")

        logger.info(f"Presence startup sweep marked {count} presence rows offline")
        return count

    def set_status(self, user_id: int, status: Union[UserStatus, str]) -> Presence:
        """
        Set user's status.

        Args:
            user_id: ID of the user
            status: New status (online, idle, dnd, offline)

        Returns:
            Updated Presence
        """
        self._validate_user(user_id)

        if isinstance(status, str):
            try:
                status = UserStatus(status.lower())
            except ValueError:
                raise InvalidStatusError(f"Invalid status value: {status}") from None
        elif not isinstance(status, UserStatus):
            raise InvalidStatusError(f"Invalid status type: {type(status).__name__}")

        now = self._get_timestamp()

        result = self._db.execute(
            "UPDATE pres_presence SET status = ?, last_seen = ?, updated_at = ? WHERE user_id = ?",
            (status.value, now, now, user_id),
        )

        if result.rowcount == 0:
            self._ensure_presence_record(user_id)
            self._db.execute(
                "UPDATE pres_presence SET status = ?, last_seen = ?, updated_at = ? WHERE user_id = ?",
                (status.value, now, now, user_id),
            )

        presence = self.get_presence(user_id, use_cache=False)
        try:
            cache_set(
                f"presence:{user_id}",
                self._presence_to_dict(presence),
                ttl=self._presence_timeout_ms // 1000,
            )
        except Exception as e:
            logger.debug(f"Failed to cache presence for user {user_id}: {e}")
        if valkey_available():
            self._update_valkey_presence(user_id, status.value)

        logger.debug(f"User {user_id} status set to {status.value}")
        return presence

    def get_status(self, user_id: int) -> UserStatus:
        """Get user's current status."""
        row = self._db.fetch_one(
            "SELECT status FROM pres_presence WHERE user_id = ?", (user_id,)
        )

        if not row:
            return UserStatus.OFFLINE

        try:
            return UserStatus(row["status"])
        except ValueError:
            return UserStatus.OFFLINE

    def clear_status(self, user_id: int) -> Presence:
        """Clear user's status (set to offline)."""
        return self.set_status(user_id, UserStatus.OFFLINE)
