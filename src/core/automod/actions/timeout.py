"""
Timeout user action.

Temporarily restricts a user from sending messages.
"""

import time
from typing import Dict, Any, Optional

import utils.logger as logger

from .base import BaseAction
from ..models import ActionType, RuleAction, Violation


class TimeoutUserAction(BaseAction):
    """Action that times out a user."""

    action_type = ActionType.TIMEOUT_USER

    DEFAULT_DURATION = 300

    def execute(
        self,
        action: RuleAction,
        violation: Violation,
        context: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Timeout the user."""
        duration = action.duration_seconds or self.DEFAULT_DURATION

        if not self._servers:
            logger.warning("Cannot timeout user: servers module not available")
            return False

        try:
            now = int(time.time() * 1000)
            timeout_until = now + (duration * 1000)

            reason = action.reason or f"Automod: {violation.rule_type.value} violation"

            self._db.execute(
                """UPDATE srv_members 
                   SET timeout_until = ?, timeout_reason = ?
                   WHERE server_id = ? AND user_id = ?""",
                (timeout_until, reason, violation.server_id, violation.user_id)
            )

            logger.debug(
                f"Timed out user {violation.user_id} in server {violation.server_id} "
                f"for {duration}s due to violation {violation.id}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to timeout user {violation.user_id}: {e}")
            return False

    def can_execute(
        self,
        action: RuleAction,
        violation: Violation,
        context: Optional[Dict[str, Any]] = None
    ) -> tuple:
        """Check if user can be timed out."""
        if not violation.server_id:
            return False, "No server ID available"

        member = self._db.fetch_one(
            "SELECT * FROM srv_members WHERE server_id = ? AND user_id = ?",
            (violation.server_id, violation.user_id)
        )

        if not member:
            return False, "User is not a member of the server"

        server = self._db.fetch_one(
            "SELECT owner_id FROM srv_servers WHERE id = ?",
            (violation.server_id,)
        )

        if server and server["owner_id"] == violation.user_id:
            return False, "Cannot timeout server owner"

        return True, None
