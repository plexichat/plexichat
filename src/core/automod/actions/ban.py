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

                member_row = self._db.fetch_one(
                    "SELECT id FROM srv_members WHERE server_id = ? AND user_id = ?",
                    (violation.server_id, violation.user_id),
                )
                # srv_member_roles is keyed by member_id (not server_id/user_id);
                # resolve the member first so role rows are actually removed.
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
                # This system path bypasses the permission-checked
                # ban_member(), so replay the cache invalidation it would
                # have done. Otherwise the removed member's cached channel
                # permissions keep granting access until their TTL expires.
                self._invalidate_member_authz(violation.server_id, violation.user_id)

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

        # Fail closed: the owner hierarchy is the only thing protecting the
        # server owner from a system/manual ban. If the server row cannot be
        # resolved (stale row, anomalous DB state, or a caller-supplied
        # server_id that does not match a real server), we cannot establish
        # that hierarchy and must not sanction anyone. Reading the row with
        # ``.get`` also avoids a KeyError on a malformed row escaping into the
        # action pipeline.
        if isinstance(server, dict):
            owner_id = server.get("owner_id")
        else:
            owner_id = server[0] if server else None

        if owner_id is None:
            return False, "Cannot resolve server owner"

        # SECURITY: both ids are authoritative identities -- owner_id comes from
        # the srv_servers row, violation.user_id from the rule violation. Loose
        # ``==`` let a text/float encoded owner id ("5", 5.9) compare unequal to
        # the genuine int target, skipping this protected-target rule and letting
        # automod ban the server owner. Fail closed: a genuine int attests to
        # identity (type() also rejects bool); a malformed value cannot prove the
        # target is NOT the owner, so deny the sanction.
        if type(owner_id) is not int or type(violation.user_id) is not int:
            return False, "Cannot verify server owner identity"

        if owner_id == violation.user_id:
            return False, "Cannot ban server owner"

        return True, None
