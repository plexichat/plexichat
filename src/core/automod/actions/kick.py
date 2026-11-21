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
        context: Optional[Dict[str, Any]] = None,
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
                    reason=reason,
                )
            else:
                # SECURITY: see the matching comment in ban.py. The
                # previous code path bypassed the server permission
                # layer via raw SQL whenever ``bot_user_id`` was
                # None. We now refuse that fallback unless explicit,
                # audit-traceable system context is supplied.
                system_context = bool((context or {}).get("__system_context__"))
                if not (self._servers and system_context):
                    logger.error(
                        "AutoMod kick REFUSED: bot_user_id missing "
                        "and no permission-checked system context "
                        "was supplied."
                    )
                    return False

                member_row = self._db.fetch_one(
                    "SELECT id FROM srv_members WHERE server_id = ? AND user_id = ?",
                    (violation.server_id, violation.user_id),
                )
                # srv_member_roles is keyed by member_id (not server_id/user_id).
                # Delete roles before the member row to respect the FK.
                if member_row:
                    member_id = (
                        member_row["id"]
                        if isinstance(member_row, dict)
                        else member_row[0]
                    )
                    self._db.execute(
                        "DELETE FROM srv_member_roles WHERE member_id = ?",
                        (member_id,),
                    )
                self._db.execute(
                    "DELETE FROM srv_members WHERE server_id = ? AND user_id = ?",
                    (violation.server_id, violation.user_id),
                )
                # This system path bypasses the permission-checked
                # kick_member(), so replay the cache invalidation it would
                # have done. Otherwise the removed member's cached channel
                # permissions keep granting access until their TTL expires.
                self._invalidate_member_authz(violation.server_id, violation.user_id)

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
        context: Optional[Dict[str, Any]] = None,
    ) -> tuple:
        """Check if user can be kicked."""
        if not violation.server_id:
            return False, "No server ID available"

        member = self._db.fetch_one(
            "SELECT * FROM srv_members WHERE server_id = ? AND user_id = ?",
            (violation.server_id, violation.user_id),
        )

        if not member:
            return False, "User is not a member of the server"

        server = self._db.fetch_one(
            "SELECT owner_id FROM srv_servers WHERE id = ?", (violation.server_id,)
        )

        # Fail closed: see the matching guard in ban.py. Without a resolvable
        # owner row the ownership hierarchy cannot be established, so the
        # action must be refused rather than silently allowed. ``.get`` keeps a
        # malformed row from raising out of the permission check.
        if isinstance(server, dict):
            owner_id = server.get("owner_id")
        else:
            owner_id = server[0] if server else None

        if owner_id is None:
            return False, "Cannot resolve server owner"

        # SECURITY: fail-closed owner/target id typing, mirroring ban.py. A
        # malformed owner or violation id could otherwise make the loose ``==``
        # below evaluate False and skip the protected-target rule.
        if type(owner_id) is not int or type(violation.user_id) is not int:
            return False, "Cannot verify server owner identity"

        if owner_id == violation.user_id:
            return False, "Cannot kick server owner"

        return True, None
