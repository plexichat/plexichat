"""
Ban user action.

Permanently bans a user from the server.
"""

import time
from typing import Dict, Any, Optional

import utils.logger as logger
from src.utils.encryption import generate_snowflake_id

from .base import BaseAction
from ..models import ActionType, RuleAction, Violation


class BanUserAction(BaseAction):
    """Action that bans a user from the server."""

    action_type = ActionType.BAN_USER

    def execute(
        self,
        action: RuleAction,
        violation: Violation,
        context: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Ban the user."""
        if not self._servers:
            logger.warning("Cannot ban user: servers module not available")
            return False

        try:
            reason = action.reason or f"Automod: {violation.rule_type.value} violation"
            bot_user_id = context.get("bot_user_id") if context else None
            delete_message_days = action.metadata.get("delete_message_days", 0)

            if bot_user_id:
                self._servers.ban_member(
                    user_id=bot_user_id,
                    server_id=violation.server_id,
                    member_user_id=violation.user_id,
                    reason=reason,
                    delete_message_days=delete_message_days
                )
            else:
                self._db.execute(
                    "DELETE FROM srv_members WHERE server_id = ? AND user_id = ?",
                    (violation.server_id, violation.user_id)
                )
                self._db.execute(
                    "DELETE FROM srv_member_roles WHERE server_id = ? AND user_id = ?",
                    (violation.server_id, violation.user_id)
                )

                now = int(time.time() * 1000)
                ban_id = generate_snowflake_id()
                self._db.execute(
                    """INSERT INTO srv_bans (id, server_id, user_id, banned_by, reason, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (ban_id, violation.server_id, violation.user_id, 0, reason, now)
                )

            logger.debug(
                f"Banned user {violation.user_id} from server {violation.server_id} "
                f"due to violation {violation.id}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to ban user {violation.user_id}: {e}")
            return False

    def can_execute(
        self,
        action: RuleAction,
        violation: Violation,
        context: Optional[Dict[str, Any]] = None
    ) -> tuple:
        """Check if user can be banned."""
        if not violation.server_id:
            return False, "No server ID available"

        existing_ban = self._db.fetch_one(
            "SELECT 1 FROM srv_bans WHERE server_id = ? AND user_id = ?",
            (violation.server_id, violation.user_id)
        )

        if existing_ban:
            return False, "User is already banned"

        server = self._db.fetch_one(
            "SELECT owner_id FROM srv_servers WHERE id = ?",
            (violation.server_id,)
        )

        if server and server["owner_id"] == violation.user_id:
            return False, "Cannot ban server owner"

        return True, None
