"""
Kick user action.

Removes a user from the server.
"""

from typing import Dict, Any, Optional

import utils.logger as logger

from .base import BaseAction
from ..models import ActionType, RuleAction, Violation


class KickUserAction(BaseAction):
    """Action that kicks a user from the server."""

    action_type = ActionType.KICK_USER

    def execute(
        self,
        action: RuleAction,
        violation: Violation,
        context: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Kick the user."""
        if not self._servers:
            logger.warning("Cannot kick user: servers module not available")
            return False

        try:
            reason = action.reason or f"Automod: {violation.rule_type.value} violation"
            bot_user_id = context.get("bot_user_id") if context else None

            if bot_user_id:
                self._servers.kick_member(
                    user_id=bot_user_id,
                    server_id=violation.server_id,
                    member_user_id=violation.user_id,
                    reason=reason
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

            logger.debug(
                f"Kicked user {violation.user_id} from server {violation.server_id} "
                f"due to violation {violation.id}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to kick user {violation.user_id}: {e}")
            return False

    def can_execute(
        self,
        action: RuleAction,
        violation: Violation,
        context: Optional[Dict[str, Any]] = None
    ) -> tuple:
        """Check if user can be kicked."""
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
            return False, "Cannot kick server owner"

        return True, None
