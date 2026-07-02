"""
Ban user action.

Permanently bans a user from the server.
"""

import time
from typing import Dict, Any, Optional

import utils.logger as logger
from src.utils.encryption import generate_snowflake_id, encrypt_data

from .base import BaseAction
from ..models import ActionType, RuleAction, Violation


class BanUserAction(BaseAction):
    """Action that bans a user from the server."""

    action_type = ActionType.BAN_USER

    def execute(
        self,
        action: RuleAction,
        violation: Violation,
        context: Optional[Dict[str, Any]] = None,
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
                    delete_message_days=delete_message_days,
                )
            else:
                # SECURITY: the previous implementation fell back to a
                # raw SQL DELETE/INSERT path whenever ``bot_user_id``
                # was None. That bypassed the server permission layer
                # entirely — any future caller (or regression) that
                # invoked ``process_violation`` for a flagging-without-
                # bot context would mutate membership without the user
                # being permission-checked. We refuse this fallback
                # unless an explicit, audit-traceable system-context
                # marker is set, AND we require the servers module to
                # be present (the layer that actually enforces roles,
                # channel-permissions and audit).
                system_context = bool((context or {}).get("__system_context__"))
                if not (self._servers and system_context):
                    logger.error(
                        "AutoMod ban REFUSED: bot_user_id missing and "
                        "no permission-checked system context was "
                        "supplied. Configure a bot or set "
                        "context['__system_context__']=True AFTER the "
                        "permission layer has approved the action."
                    )
                    return False

                self._db.execute(
                    "DELETE FROM srv_members WHERE server_id = ? AND user_id = ?",
                    (violation.server_id, violation.user_id),
                )
                self._db.execute(
                    "DELETE FROM srv_member_roles WHERE server_id = ? AND user_id = ?",
                    (violation.server_id, violation.user_id),
                )

                now = int(time.time() * 1000)
                ban_id = generate_snowflake_id()
                self._db.execute(
                    """INSERT INTO srv_bans (id, server_id, user_id, banned_by, reason, reason_encrypted, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ban_id,
                        violation.server_id,
                        violation.user_id,
                        # 0 here records the action as system-issued.
                        # Audit consumers should treat banned_by=0 as
                        # an automod action rather than a user action.
                        0,
                        reason,
                        encrypt_data(reason, context=f"ban:{ban_id}")
                        if reason
                        else None,
                        now,
                    ),
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
        context: Optional[Dict[str, Any]] = None,
    ) -> tuple:
        """Check if user can be banned."""
        if not violation.server_id:
            return False, "No server ID available"

        existing_ban = self._db.fetch_one(
            "SELECT 1 FROM srv_bans WHERE server_id = ? AND user_id = ?",
            (violation.server_id, violation.user_id),
        )

        if existing_ban:
            return False, "User is already banned"

        server = self._db.fetch_one(
            "SELECT owner_id FROM srv_servers WHERE id = ?", (violation.server_id,)
        )

        if server and server["owner_id"] == violation.user_id:
            return False, "Cannot ban server owner"

        return True, None
