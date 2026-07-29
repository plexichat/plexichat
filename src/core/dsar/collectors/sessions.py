"""
Sessions collector for auth_sessions, auth_devices, auth_known_ips tables.

Collects session data, registered devices, and known IP addresses.
"""

from typing import Any, Dict, List

from ..base import BaseCollector


class SessionsCollector(BaseCollector):
    """Collects session, device, and known IP data."""

    def collect(self, user_id: int) -> Dict[str, Any]:
        """Collect sessions, devices, and known IPs."""
        return {
            "sessions": self._collect_sessions(user_id),
            "devices": self._collect_devices(user_id),
            "known_ips": self._collect_known_ips(user_id),
        }

    def _collect_sessions(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect auth_sessions."""
        try:
            rows = self._db.fetch_all(
                """
                SELECT id, device_id, ip_encrypted, ua_encrypted, created_at,
                       expires_at, last_activity, revoked
                FROM auth_sessions WHERE user_id = ?
                """,
                (user_id,),
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect sessions for user {user_id}: {e}")
            return []

    def _collect_devices(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect auth_devices."""
        try:
            rows = self._db.fetch_all(
                """
                SELECT id, fingerprint, name, device_type, first_seen_at, last_seen_at
                FROM auth_devices WHERE user_id = ?
                """,
                (user_id,),
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect devices for user {user_id}: {e}")
            return []

    def _collect_known_ips(self, user_id: int) -> List[Dict[str, Any]]:
        """Collect auth_known_ips."""
        try:
            rows = self._db.fetch_all(
                """
                SELECT id, ip_encrypted, first_seen_at, last_seen_at
                FROM auth_known_ips WHERE user_id = ?
                """,
                (user_id,),
            )
            return [dict(row) for row in rows]
        except Exception as e:
            import utils.logger as logger

            logger.error(f"Failed to collect known IPs for user {user_id}: {e}")
            return []
